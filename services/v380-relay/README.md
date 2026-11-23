# V380 relay

A container that serves one V380 camera to the rest of the stack, built only from our own V380 client in packages/camera-adapters. go2rtc runs inside it as a child process with two exec sources, one per stream, that run python -m camera_adapters.v380 stream; each writes the camera's H.265 or H.264 video as an Annex-B byte stream to its standard output, which go2rtc reads directly and serves over RTSP. A small HTTP API takes PTZ, light, siren and picture commands and hands them to the process that holds the camera's session, so the camera keeps one session per stream. The protocol is specified in docs/protocols/v380.md.

The relay is the all-in-one deployment's way to run V380 cameras without the earlier bridge. A connector needs no relay: its own go2rtc runs the same exec source.

## Run

The compose services v380-relay-ptz-1 and v380-relay-ptz-2 of deploy/server run behind the v380-native profile, beside the bridge services, which stay the default until the cut-over; from deploy/server:

```
docker compose --profile v380-native up -d --build v380-relay-ptz-1 v380-relay-ptz-2
docker compose logs -f v380-relay-ptz-1
```

| Address in the stack | Serves |
|---|---|
| rtsp://smart-surveillance-system-v380-relay-ptz-1:8554/main | Main stream |
| rtsp://smart-surveillance-system-v380-relay-ptz-1:8554/sub | Sub stream |
| http://smart-surveillance-system-v380-relay-ptz-1:8080/api/status | Status and the facts of the open sessions |

A camera served by a relay is registered with the relay as its host, RTSP port 8554 and the paths /main and /sub. go2rtc in the relay starts a stream's session when the first consumer arrives and ends it when the last one leaves. Whether a camera serves its main and sub stream to two sessions at once is not yet verified, so a site that needs only one stream per camera serves only that one with SSS_V380_STREAMS.

## API

| Request | Effect |
|---|---|
| GET /api/status | running when a session is open or the camera answers, otherwise unreachable, with the facts of each open stream |
| POST /api/ptz/left, right, up, down or stop | Starts a move along one axis or stops it; a move that is neither stopped nor renewed stops by itself after 30 seconds |
| POST /api/light/on, off or auto | Switches the light |
| POST /api/siren/on?seconds=10 | Sounds the siren for the given seconds, up to 300 |
| POST /api/siren/off | Silences the siren |
| POST /api/picture/colour, black_and_white or auto | Switches the picture mode |
| POST /api/flip | Turns the picture by 180 degrees |

Commands answer 200 when sent, 400 for an unknown value, 404 for an unknown command and 503 when the camera cannot be reached. When no stream runs, the relay opens a sub stream session of its own for the command and closes it 30 seconds after the last one.

## Test

The relay and the client are tested against the device simulator in tools/devkit/v380:

```
cd tools/devkit/v380
uv run --package camera-adapters pytest -q
```

## Cut-over on the real cameras

The relay replaces the bridge services only after these steps pass on the real cameras, with the owner on site. Run them with both relays up beside the bridges, then stop the bridges.

1. Start the relays from deploy/server: docker compose --profile v380-native up -d --build v380-relay-ptz-1 v380-relay-ptz-2, and check that GET /api/status answers running for both.
2. Sessions: on both cameras, device version 31 and device version 32, check with ffprobe that rtsp://RELAY:8554/main and /sub play H.265 at the sizes the specification lists, and that the logs show the short stream login reply and no reconnect loop.
3. Two streams at once: play /main and /sub of the same camera together for ten minutes. If either drops or the camera restarts, set SSS_V380_STREAMS to the one stream the site needs.
4. Registration: change each camera to the relay as host, RTSP port 8554 and the paths /main and /sub, in place of the bridge and /live, and check live view, analytics and recordings.
5. PTZ: move each camera left, right, up and down and release. Every move must stop, including when the viewer leaves the camera, when the stream process exits and after the 30 s guard. The API moves bridged cameras over ONVIF; a camera whose PTZ came from the bridge's ONVIF needs the API to call the V380 adapter or the relay's /api/ptz before the cut-over. Presets and a home position exist only where a camera's own ONVIF offers them.
6. Light, siren, picture and flip: try each on each camera and note what has an effect, whether the siren needs siren on every 500 ms, and which model has no siren. None of these commands is verified yet.
7. Reconnects: restart a camera and interrupt its Wi-Fi; the relay must resume within the back-off of 1, 2, 5 and 10 s and then 30 s, and go2rtc must keep its streams.
8. Refusals: give one relay a wrong password and check that it reports the refusal and retries no more than every 300 s.
9. TV: on the real TV, check that both cameras play, that full view falls back to the sub stream where the main stream exceeds the decoder's 1920x1088 limit, that sound plays (IMA ADPCM on the version 32 camera, AAC on the version 31 camera), that the low-latency SPS rewrite takes effect, that a held direction key moves and its release stops, and that the V380 search finds both cameras, also after one moves to another address.
10. Remove: once every step passes, stop and remove the bridge services and services/v380-bridge, rebuild the TV APK and record the results.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| SSS_V380_HOST | required | Camera address |
| SSS_V380_PORT | 8800 | Camera TCP port |
| SSS_V380_DEVICE_ID | required | Device id from the camera label |
| SSS_V380_USERNAME | admin | Camera user name |
| SSS_V380_PASSWORD | required | Camera password |
| SSS_V380_STREAMS | main,sub | Streams to serve |
| SSS_V380_RTSP_PORT | 8554 | RTSP port |
| SSS_V380_RTSP_USERNAME | empty | RTSP user, no RTSP login when empty |
| SSS_V380_RTSP_PASSWORD | empty | RTSP password |
| SSS_V380_API_PORT | 8080 | Port of the HTTP API |
| SSS_V380_GO2RTC | /usr/local/bin/go2rtc | go2rtc program |
| SSS_V380_GO2RTC_API | 127.0.0.1:1984 | Loopback address of go2rtc's own API |
| SSS_V380_CONTROL_DIR | /tmp/sss-v380 | Directory of the control sockets and the password file |

The compose services fill the camera variables from V380_PTZ_1_HOST, V380_PTZ_1_ID, V380_PTZ_2_HOST, V380_PTZ_2_ID, V380_USERNAME and V380_PASSWORD, the same variables the bridge services read.
