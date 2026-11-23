# Analytics

Video analytics of Smart Surveillance System on NVIDIA DeepStream 9.1 (the newest DeepStream image on nvcr.io, with CUDA
13.2 and TensorRT 10.16): people, vehicle and animal detection, tracking, zones,
lines, loitering, crowding and tamper watch, with an event clip and snapshot for every event.

## How it works

- The service reads each camera's original H.265 stream from go2rtc's RTSP server and decodes it once on NVDEC.
- A multi-lens camera sends its lenses stacked in one frame. A tee and one cropping nvvideoconvert per lens turn every
  lens into its own source in the batch, so the tracker never mixes objects across lenses and each lens has its own
  zones.
- Each view drops frames to its analysis rate (8 fps by default) and ends in a leaky queue, and nvurisrcbin
  reconnects a lost stream by itself, so one dead camera never holds up the others.
- The batch runs through one shared detector, RF-DETR Medium at 1024x576 with a custom bounding box parser, and the
  NvDCF tracker. A probe after the tracker hands every frame's tracked objects to the service.
- The pipeline runs in a child process. A crash in native code takes down only the pipeline, which the service
  restarts, and a configuration change restarts it only when the decoded or analysed views change.
- The rules engine in app/rules is plain Python with no DeepStream import, so it runs on any host that produces
  tracked boxes and is tested without a GPU. Zone and rule changes apply without touching the pipeline.
- One ffmpeg process per camera copies the stream into two-second segments without re-encoding and decodes only the
  keyframes into small grey pictures for the tamper watch. An event's snapshot and clip are cut from these segments:
  the clip starts a few seconds before the event, is cropped to the event's lens and is encoded once to H.264 MP4 with
  NVENC, or with libx264 where no hardware encoder exists, such as a Jetson Orin Nano.

## Profiles

| Profile | Stages |
|---|---|
| perimeter | detector, tracker, zones, lines and rules for people, animals and vehicles, tamper watch |
| vehicle | detector, tracker, zones, lines and rules for vehicles, tamper watch |
| identity | detector, tracker and rules for people, and the face stage |
| tamper | tamper watch only, without the detector |

## Rules

- A zone is a polygon with a class filter, a minimum dwell, an optional loitering time, an optional crowd count, a
  schedule and an alert policy. An object is inside when its foot point, the bottom centre of its box, is.
- An intrusion opens when an object has stayed inside for the dwell and closes once nothing qualifying has been inside
  for the close delay; each zone keeps one event per label, and vehicles open vehicle events.
- Vehicles, and anything already in the picture when a view starts, count only after they move, and objects that stay
  in place for minutes become part of the scene, so parked cars and standing false detections stay silent.
- A line counts crossings in both directions. Crossing from the negative to the positive side is in; for a line drawn
  from left to right that means moving down the picture. An object must clear the line by a margin on both sides and
  its path must cut the drawn segment.
- Loitering and line crossings are instant events, debounced per object. A track the tracker loses and finds again
  near the same place within a few seconds stays the same object.
- Arm modes: disarmed never alerts, home alerts for policies that always alert, schedule adds the policies whose alert
  windows are active, and away alerts for every event.

## Faces

Face recognition runs on every view whose profile is identity and on any view that turns faces on.

- SCRFD 10G runs as a secondary nvinfer on the tracked person boxes of those views, at 640 by 640 with the person
  scaled in without distortion, and attaches its raw outputs to each person. Running it on person boxes instead of
  whole frames costs nothing on empty frames, gives a face far more detector pixels than a 1080p frame scaled to
  640, and hands every face its person's track id.
- A probe after the face detector takes the face in the upper part of each person box, judges it by size, landmark
  frontality, sharpness and score, and only when it beats what the track already sent copies the region around it
  out of the frame, aligns it to the 112 by 112 template with a similarity transform from its five landmarks and
  sends it to the service. A track sends its first usable face at once, a clearly better one at any time, a
  comparable one every 2 seconds and at most 12 in all.
- The service keeps the 5 best faces of each track, embeds them with glintr100 through TensorRT and decides the
  track after 3 faces or 3 seconds from the quality-weighted mean of their embeddings. A track that ends as unknown
  is checked again with all its faces and named when they match.
- A known face needs a similarity of 0.45 to one of the person's samples, 0.50 when the face is grey from night
  vision, and a lead of 0.08 over the next person. Everything else is unknown, joins the closest unknown cluster at
  0.45 or starts a new one, and records the closest person from 0.30. A track whose faces average below 0.35 in
  quality never starts a cluster. The same person or cluster makes at most one event per view within the cooldown.
- The thresholds come from the 6000 pairs of a public face verification benchmark, each face also shrunk to a 40
  pixel door-camera face and turned grey: different people never scored above 0.27, and the same person scored
  0.63 to 0.71 in the median, so 0.45 keeps a wide gap to strangers and lookalike relatives while accepting about 98
  percent of small daylight faces and 92 percent of small grey faces against an enrolled photo.
- Face events, crops and the gallery of known people and unknown clusters live in the backend. The service polls
  the gallery, posts one face result per decided track and writes the 160 pixel crop under faces/ in the events
  folder. POST /identity/embed with a photo as the body answers its faces with boxes, landmarks, quality and
  embedding, which the backend uses to enroll people.
- The two engines take about 270 MB of GPU memory, and embedding a face takes 1 to 2.5 ms.

The models are the InsightFace antelopev2 pack, which the InsightFace model zoo publishes for non-commercial research
use only; scripts/download-face-models.sh fetches and checks them.

## Features beyond zones and lines

The hub in app/features.py runs these per view, beside the rules engine, and hands their events to the service like
any other event.

- Tamper (app/tampering): every view, sampled from the keyframe thumbnails at most every 5 s. Covered means flat
  against what the view normally shows at that brightness, too dark means below an absolute floor and well below the
  darkest regime learned, blurred means the edges collapsed while the place still matches, and moved means the edge
  structure no longer lines up. A reference is learned per lighting regime, day and infrared night, and a regime
  switch starts a grace. Views with too little structure, such as a blank wall, never report blur or movement, and
  moving lenses never report movement.
- Counts (app/counts): every counted line crossing goes into a minute bucket per line, direction and label; the
  backend upserts the absolute totals of each run of the service and sums them into hours, days and area occupancy.
- Objects left and taken (app/objects): per zone with left_seconds or taken_seconds. Still objects are tracked by
  place rather than tracker id. Objects already there when a view starts never raise left, and taken needs a person
  near the place or the object seen leaving, so parked cars and boxes stay silent. Zones with background set also
  compare the keyframes with a slow background model for objects the detector has no class for.
- Animals (app/rules/animals.py): dogs, cats and birds open animal events. A zone or line sets animals to record
  (the default: events without alerts in any arm mode), include (events that may alert) or exclude (no events).
- Falls, beta (app/falls): RTMPose-m on the person box widened by a quarter to 3 by 4, cropped from the RGB frame in
  the pipeline process because nvinfer can only feed the bare box; on real crops the widened box found 72 percent of
  the keypoints above 0.3 against 45 percent for the bare box. A fall is a change from upright to lying within 1.5 s
  with the hips dropping, then lying still for 8 s.
- Number plates, beta (app/plates): read on vehicle events from full-resolution pictures of the recording buffer,
  because the 1080p muxer frame shrinks Camera B's plates by a third. PaddleOCR's DB text detector finds plate-shaped
  text lines, each line is read as upright crops and levelled into a straight band of characters, because a plate
  seen from above leans and an upright box takes in the grille, the PP-OCRv4 English recogniser reads them, the text
  is fitted to the Uzbek formats (01 A 123 BC and 01 123 ABC) with look-alike letters and digits swapped only where
  the format needs the other kind, and the reads of an event vote. Grey infrared pictures are not read.
- Journeys, beta (app/reid): OSNet-AIN x1.0 embeds person boxes about once a second in the pipeline process. Raw
  cosine similarity is about 0.5 between any two people on these cameras, so tracks are compared after taking away
  the running mean of all tracks; then unrelated tracks score near 0 and the same person in the same light 0.2 to
  0.5. A finished track links to the mutual best match of another view within 3 minutes above 0.35, and nothing
  links before 20 tracks have been seen. Colour and infrared pictures of the same person do not match.
- Auto-follow (app/follow): off by default. The controller keeps the chosen person centred with a dead zone,
  proportional speed between 0.15 and 0.5 and a stop after 1 s without the person, saves the position when it starts
  and returns there after 30 s without anyone by retracing its moves, one reverse command per move, because PTZ 1
  answers GotoPreset without moving and turns a fixed step per move command; a preset return can be turned on per
  view with presets in the follow settings. The backend sends the moves to the camera's own ONVIF device when it
  offers PTZ and to the bridge otherwise.

| Model | Use | Licence | Engine memory | Batch time |
|---|---|---|---|---|
| OSNet-AIN x1.0 MSMT17 | appearance | MIT | 118 MiB | 0.8 ms for 1, 2.5 ms for 16 |
| RTMPose-m body7 256x192 | pose | Apache 2.0 | 26 MiB | 0.7 ms for 1, 1.8 ms for 8 |
| PaddleOCR PP-OCRv4 det mobile | plate finder | Apache 2.0 | measured with the plates | single pictures |
| PaddleOCR PP-OCRv4 en rec mobile | plate text | Apache 2.0 | 42 MiB | 0.4 ms for 1, 0.8 ms for 8 |
| NVIDIA LPDNet USA pruned v2.2 | optional plate finder | NVIDIA AI Enterprise EULA | 78 MiB | 0.4 ms for 1 |

scripts/download-secondary-models.sh downloads them with pinned checksums; LPDNet only with --all. Engines are built
on first use into the engine volume after the ONNX digest is checked.

## Run

The service runs as the analytics service of deploy/server/docker-compose.yml, which reads its views, zones and lines
from the backend. Download the models first:

```
scripts/download-models.sh
scripts/download-face-models.sh
scripts/download-secondary-models.sh
docker compose -f deploy/server/docker-compose.yml up -d --build analytics
```

The service polls its configuration from the backend over HTTP every 5 s and publishes every event change and every
view's observation on the event bus at SSS_NATS_URL, on the subjects packages/event-bus/README.md lists, through a
publisher that retries and keeps up to 2000 changes in order while the bus is away. Without SSS_NATS_URL it posts
them to the backend's internal intake routes instead, which publish them on the bus. Face results, counts, journeys
and follow moves stay HTTP requests to the backend. Without SSS_ANALYTICS_BACKEND_URL the service reads the same
configuration from the JSON file in SSS_ANALYTICS_CONFIG_FILE instead, for a host without the backend. The first start builds the TensorRT engine into
the engine volume, which takes about a minute.

## Configuration

The variables in deploy/server/.env.example, from the model folders to the clip lengths, are described in the
configuration reference of the repository's README. The service also reads these, which the example does not list, so
they keep their defaults unless set:

| Name | Default | Meaning |
|---|---|---|
| SSS_ANALYTICS_BACKEND_URL | empty | Backend the configuration comes from and faces, counts and journeys go to; compose sets it |
| SSS_ANALYTICS_CONFIG_FILE | /config/analytics.json | JSON configuration read when no backend is set |
| SSS_ANALYTICS_RTSP_URL | rtsp://go2rtc:8554 | go2rtc RTSP server the streams are read from |
| SSS_ANALYTICS_ENGINE_BATCH | 8 | Largest batch the detector engine is built for |
| SSS_ANALYTICS_WORKSPACE_MB | 1024 | TensorRT builder workspace |
| SSS_FACES_MIN_SCORE | 0.6 | Lowest face detection score |
| SSS_FACES_MIN_SIZE | 36 | Smallest face height in pixels at the muxer resolution |
| SSS_FACES_MIN_QUALITY | 0.15 | Lowest quality of a face the pipeline sends |
| SSS_FACES_CLUSTER_QUALITY | 0.35 | Mean face quality a track needs to start a cluster |
| SSS_FACES_KNOWN_THRESHOLD | 0.45 | Similarity that names a person |
| SSS_FACES_IR_EXTRA | 0.05 | Added to the known threshold for grey faces |
| SSS_FACES_MARGIN | 0.08 | Lead over the next person needed to name one |
| SSS_FACES_CANDIDATE_THRESHOLD | 0.3 | Similarity from which an unknown face records its closest person |
| SSS_FACES_CLUSTER_THRESHOLD | 0.45 | Similarity that joins an unknown cluster |
| SSS_FACES_COOLDOWN_SECONDS | 600 | Seconds during which a person or cluster makes one event per view |

## Status

GET /status on port 8080, with the shared token as a bearer token, returns the pipeline state, per view frame rate, latency, objects, rule counts, tamper
readings and errors, the recorders, the GPU reading, recent events, the bus publisher's counters and the face stage's
models, gallery, counts and recent face results. GET /views/{view}/objects returns the tracked
boxes of a view's latest frame, and GET /views/{view}/snapshot.jpg a fresh picture of the view, kept as a zone
reference with reference=1.

## Tests

```
docker build --target test --build-context event-bus=packages/event-bus -t smart-surveillance-system-analytics:test services/analytics
docker run --rm smart-surveillance-system-analytics:test
```
