# Smart Surveillance System

## Commands

Run the self-hosted stack from its folder, deploy/server; deploy/README.md lists every deployment target and its commands:

```
cd deploy/server
docker compose up -d --build
docker compose logs -f backend
docker compose down
```

The host has no Node or project Python environment; run tools in containers as your own user, from the repository root.

```
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache -v $PWD:/repo -w /repo/services/api ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv sync --frozen --all-packages && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen pytest -q"

docker build --target test -t smart-surveillance-system-analytics:test services/analytics
docker run --rm -u $(id -u):$(id -g) -e RUFF_CACHE_DIR=/tmp/ruff-cache -v $PWD/services/analytics:/work:ro -w /work --entrypoint sh smart-surveillance-system-analytics:test -c "ruff check . && ruff format --check . && pytest -q -p no:cacheprovider"

docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e COREPACK_HOME=/tmp/corepack -e PATH=/tmp/bin:/usr/local/bin:/usr/bin:/bin -v $PWD/apps/console:/app -w /app node:24 sh -c "mkdir -p /tmp/bin && corepack enable --install-directory /tmp/bin && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm build"
```

Run the connector agent's and the camera adapters' checks the same way, with -w /repo/services/connector or -w /repo/packages/camera-adapters in place of the API folder. Write the OpenAPI document after changing a route or schema, because the API tests compare it with the app:

```
scripts/export-openapi.sh
```

Run the development kit, the compose project smart-surveillance-dev in deploy/devkit with a simulated site, from the repository root; tools/devkit/README.md describes it:

```
scripts/devkit.sh init
scripts/devkit.sh up
scripts/devkit.sh seed
scripts/devkit.sh logs -f backend
```

The TV app builds and tests with the Android Studio JDK and SDK, as apps/tv/README.md describes:

```
cd apps/tv && ./gradlew testDebugUnitTest
```

The mobile app builds with Flutter 3.47 and the Android SDK, as apps/mobile/README.md describes:

```
cd apps/mobile && flutter pub get && flutter analyze && flutter test
```

Create a migration from services/api/ against a database already at head, with the stack's postgres or a temporary one on the same network:

```
alembic upgrade head
alembic revision --autogenerate -m "<message>"
```

## Rules

### Repository

- Keep the public repository free of the private data of the sites it watches: no real LAN addresses (examples and tests use 192.168.1.x), device ids, MACs, serials, Wi-Fi names, passwords, tokens, number plates, names, workspace paths, and no images, video or faces from the real cameras; tests use synthetic pictures or public-domain photos, because the repository is published and history cannot be taken back without a rewrite.
- Keep the .env of every deploy target and apps/tv/cameras.local.properties out of git and never print their values, because they hold the camera and service passwords.
- Never bypass the local pre-push hook; when it refuses, remove the private value from the commits and push again, because the hook is the last check before data becomes public.
- Use only models whose licence allows a public repository and keep every other model optional behind an explicit download flag, and never use Ultralytics YOLO weights or code, because their AGPL licence would bind the whole project.
- Keep model weights and engines out of git; scripts/download-models.sh, download-face-models.sh and download-secondary-models.sh fetch them with pinned checksums and print each licence.
- Give every Python module, class and function outside tests and migrations a docstring with Args, Returns, Yields, Raises and Attributes sections where they apply, and add a comment only where the code cannot show its reason, because ruff enforces the docstring convention and the docstrings are the code's reference; write no emojis or non-ASCII characters in code, configs or docs.

### Deployment

- Keep one folder per deployment target in deploy, each with one docker-compose.yml that runs on its own and its .env.example, and give a target the server's services with extends from ../server/docker-compose.yml, never by copying them or stacking compose files, because a change to a service must reach every target.
- Set env_file to the target's own .env in every service a target extends, because the server's relative .env resolves to deploy/server/.env.
- Require a variable with :? in deploy/server/docker-compose.yml only when every target that extends the server needs it, because compose interpolates the whole server file for each of them.
- Keep the compose project names smart-surveillance-system, smart-surveillance-connector and smart-surveillance-dev and the container, volume, network and image names, because the sites and the kit keep their data in the projects' named volumes.

### Backend

- The backend follows the canonical FastAPI module layout: routers call services, services own the transaction, models register in app/models/__init__.py, because Alembic and the import order depend on that registry.
- Never hand-write or edit a migration; commit the autogenerated file as written, because a wrong migration loses the registry.
- Serve /internal only from the backend, never through nginx, and require SSS_ANALYTICS_TOKEN on the internal analytics routes, because they write events, faces and the gallery without a user session.
- Keep camera links in the health module and read camera fields there without changing the cameras module, because the health page must not touch the registry the wall and the TV depend on.
- Delete person appearances and journeys with the event retention and keep line counts on their own longer retention, because Find this person must never offer a sighting whose event is gone, while counts are small and serve comparisons over months.

### Platform

- Resolve every route's site with get_site_access, or its organisation with resolve_organisation_access, and check the role there before anything else, because the request's session is limited to that site only after the access is resolved, and every query relies on that scope; read past it only with the unscoped execution option in jobs and platform admin code.
- Check plan limits and features only through the billing entitlements, require_capacity and require_feature, and apply the same entitlements in the analytics configuration, because a feature a plan leaves out must be neither offered nor run.
- Add audit records, never update or delete them outside the retention pruner, and record an action only after it succeeded, because the activity log is the organisation's evidence of who did what.
- Keep camera passwords of connector cameras on the connector, and let connector discovery sweep only site LAN interfaces, never Docker, container, Kubernetes or VPN interfaces unless SSS_CONNECTOR_INTERFACES lists them, because a sweep of a VPN or bridge network probes networks that are not the site's.
- Test Telegram alerts only against the fake Bot API in tools/devkit/telegram or a mock transport, never with a real bot token, because a second poller of a live bot takes its updates away from the service that owns it.
- Show only the simulated site in screenshots, demos and seed data, because the repository is public.

### Streaming

- Keep camera streams out of every go2rtc.yaml under deploy and the files mounted read-only, because go2rtc writes stream URLs with camera passwords into its config when it can; only the TV exec streams, which carry no credentials, live there.
- Keep the player asking for H.264 only, because go2rtc's direct H.265 path from the V380 delivers no frames.
- Keep rtsp.listen on ":8554", which includes loopback, because every ffmpeg transcode and TV exec source reads and publishes through go2rtc's RTSP server on 127.0.0.1, and go2rtc skips RTSP authentication only for loopback clients.
- Publish go2rtc's RTSP port only on SSS_LAN_BIND and keep SSS_TV_RTSP_PASSWORD set, because clients arriving through the published port are the only ones go2rtc asks for credentials and the VPN must not reach the cameras.
- Proxy the video websocket with the browser's Host header, because go2rtc rejects websocket origins that do not match it.
- Keep transcodes video only, because bridged V380 cameras advertise an audio track they never send and an audio transcode then waits until go2rtc kills it.
- Keep the go2rtc h264 template on h264_nvenc with CPU decoding, because software x264 on the 5 MP streams misses go2rtc's start timeout and GPU decoding fails on the PTZ 1 bridge stream.

### Composed TV streams

- Define the TV streams as exec sources in deploy/server/go2rtc/go2rtc.yaml that run deploy/server/go2rtc/go2rtc-tv.sh, because go2rtc 1.9.14 refuses exec sources and sources with spaces through its API.
- Keep -flags low_delay on every TV ffmpeg input, the filter threads at 2 and bilinear scaling, because low_delay turns off frame-threaded decoding, which delays each picture by a frame per thread, and the default filter threads with bicubic scaling cost about three CPU cores on the wall instead of under one.
- Compose the TV streams as overlays on a lavfi canvas, never with xstack, because xstack sends nothing until every input has delivered a frame, so one silent camera would hold the whole wall.
- Keep the TV composer's input user agent free of the RTSP username, because go2rtc masks the username wherever it appears in its API output and the backend finds the composer's inputs by that user agent.

### Cameras

- Build the V380 bridge from the pinned upstream pull request commit in services/v380-bridge/Dockerfile, because the released decoder stalls on short stream login replies and the camera resets the connection.
- Never publish bridge ports and keep the bridges' API on the internal network, because their RTSP, ONVIF and siren API have no authentication.
- Probe a bridged camera's health on its device address and port 8800, never on the bridge, because the bridge container answers even when the camera is gone.
- Never open an event from a state a camera reports when a subscription starts, because its start time is unknown and every reconnect would repeat it.
- Use this computer's clock for event times and keep the camera's time as a field, because camera clocks drift or follow a vendor time zone.
- Keep SD card playback behind SSS_REPLAY_ENABLED and one session per camera, because the cameras reset local playback sessions and go2rtc 1.9.14 keeps redialling a failed source after its stream is removed, which can make a camera restart.
- Never test reboot, siren or light on the real cameras, never open extra sessions on them for tests, and read and restore every camera setting a test changes, because the cameras restart or drop sessions under load.

### Analytics pipeline

- Run exactly one analytics pipeline: the compose analytics service. A second copy reading the cameras doubles the RTSP sessions and the GPU load.
- Keep the analytics image on nvcr.io/nvidia/deepstream 9.1-samples-multiarch with NVIDIA's user_additional_install.sh, because the samples image strips the codec libraries ffmpeg needs and has no 9.2 tag.
- Read the cameras' original H.265 streams from go2rtc and decode them on NVDEC, never the H.264 transcodes, because a transcode costs an encoder session per consumer and loses detail.
- Run the DeepStream pipeline in a child process that the service restarts, because a crash in native code must not stop the rules engine, the recorders or the status endpoint.
- Build TensorRT engines in the analytics service, keyed by TensorRT version and GPU architecture, never through nvinfer, because nvinfer resolves the ONNX path and saves engines beside the read-only model mount, so it would rebuild on every start.
- Run probes that load downloaded models under prlimit --as of 96 GB with a container memory limit, because TensorRT engine builds fail under a 32 GB address space cap.
- Keep the recorder's wall clock timestamps and its segment watchdog, because the bridged V380 streams can start without timestamps, which stops the segment output while ffmpeg keeps running.
- Cut clips and snapshots from the recorder's rolling copy-mode segments, never with DeepStream smart record, because smart record allows one session per source and has a reported memory leak on this GPU generation.
- Encode clips once to H.264 MP4 with NVENC and fall back to libx264, because browsers do not all play H.265 and a Jetson has no hardware encoder.
- Take an event's snapshot from the next segment's first frame when its time falls past the last frame of a segment or before the oldest one, because segment start times are estimates and a restart leaves the buffer short.

### Analytics rules

- Evaluate zones and lines in services/analytics/app/rules, never in nvdsanalytics, because zone edits must apply without a pipeline restart and the rules must run on a Jetson.
- Count an object only after the detector itself found it 4 times within 2 s, keep the person threshold at 0.45 and ignore person boxes inside a vehicle above its bottom edge, because NvDCF carries a lost box for a few frames and seats seen through a windscreen are detected as people.
- Let vehicles, and anything already in the picture when a view starts, count only after they move away from their rest point, because parked cars and standing false detections otherwise fire from box jitter.
- Keep zones on a moving lens tied to the reference picture they were drawn on and suspend them while the view no longer matches it, because a zone is valid only where the lens pointed when it was drawn.
- Restart a moving lens's tamper reference when its picture stops lining up with it, because the PTZ lenses also turn by themselves and a plain new place otherwise looks like a blurred old one.
- Record animals without alerts unless a zone or line includes them, because a cat in the yard at night must not wake anyone.

### Faces

- Run SCRFD as a secondary detector on tracked person boxes, never on whole frames, because it costs nothing on empty frames, gives a face far more detector pixels and hands every face its person's track id.
- Decode SCRFD outputs at the detection threshold and cap the candidates before NMS, because decoding every anchor once stalled the pipeline's streaming thread on the first person.
- Embed only the few best faces of each track and name a person at 0.45, or 0.50 for grey infrared faces, with a 0.08 lead over the next person, because these thresholds come from a calibration on public face pairs that keeps a wide gap to strangers; change them only after a new calibration.
- Preload the face models whenever a view has faces on, because the first enrollment after a fresh engine cache otherwise outlasts the backend's timeout.
- Keep enrolled face samples until their person is deleted and camera face samples on the event retention, and wipe a deleted person's embeddings and crops, because faces are the most sensitive data the system holds.

### Later analytics features

- Read number plates from full-resolution pictures of the recording buffer and never from infrared pictures, because the 1080p muxer frame shrinks distant plates by a third and infrared light makes plates glare white.
- Crop pose inputs from the RGB frame with the person box widened by a quarter, never through an nvinfer object crop, because nvinfer feeds only the bare box and RTMPose then finds far fewer keypoints.
- Compare appearance vectors only after centring them on the running mean of all tracks, because raw OSNet cosine is about 0.5 between any two people on these cameras.
- Keep NVIDIA LPDNet optional behind scripts/download-secondary-models.sh --all, because its licence is the NVIDIA AI Enterprise EULA, not an open licence.
- Keep auto-follow off by default and move a lens only through the follow controller, which records its moves and retraces them, because the owner decides whether the cameras may move by themselves.
- Return auto-follow by retracing its moves with one reverse command per move, not by GotoPreset, because PTZ 1 answers GotoPreset on its ONVIF device without moving and turns a fixed step of about a tenth of the picture per ContinuousMove, whatever its speed and duration.
- Send follow moves to a camera's own ONVIF device when it offers PTZ and to the bridge otherwise, and remember the choice per camera, because a camera without device ONVIF answers only after a timeout.

### Mobile app

- Organise apps/mobile/lib by business domain, lib/features/<domain>/{data,domain,presentation}, with shared code only in lib/core and the app's bootstrap, router, theme and strings in lib/app, because a domain then changes in one folder and the screens never import another domain's data layer.
- Give every public member a doc comment and keep public_member_api_docs on, and add an inline comment only where the code cannot show its reason, because the doc comments are the app's reference.
- Never edit lib/core/network/api by hand; regenerate it with scripts/generate-api.sh from packages/contracts/openapi.json, because the client must follow the contract exactly.
- Keep every repository behind its domain interface with a simulated fake_ and a server api_ implementation, because the app must run and be screenshotted without a server and the fakes are the UI's test data.
- Send every request through the shared Dio client, which adds the bearer token and the X-Site header and refreshes the session once for all requests that fail together, because the server revokes a session whose refresh token is used twice.
- Keep tokens in flutter_secure_storage only, never in shared preferences or logs, because they unlock live video of the user's sites.
- Add a pairing method as its own folder in lib/features/devices/pairing/methods/<method_id> behind the PairingMethod interface, show only the methods the server's catalog returns and report every attempt to the pairing telemetry, because the catalog decides what a site can use and the telemetry shows which methods fail.
- Keep the foss flavor free of Google services and Firebase, and keep push behind the push_bridge package, because the foss build must be publishable without proprietary code.
- Keep golden images, screenshots and APKs out of git, because the pre-push hook refuses media outside the resource folders.
- Write plain ASCII in Dart code and escape non-Latin characters that code must recognise as \u sequences; letters of every language stay in the ARB files, and Uzbek uses U+02BB and U+02BC for its apostrophes.
