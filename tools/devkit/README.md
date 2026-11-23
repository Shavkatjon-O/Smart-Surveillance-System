# Development kit

A whole stack on one machine with a simulated site in place of real cameras, run as the compose project smart-surveillance-dev from deploy/devkit, which takes the server's services from deploy/server and adds the simulated site. The root README describes the product; deploy/README.md describes the deployment targets.

| Folder | Holds |
|---|---|
| camera | A container image that simulates one IP camera with ONVIF, WS-Discovery and RTSP, as camera/README.md describes |
| v380 | A simulator of a V380 camera's LAN protocol and its container image, as v380/README.md describes |
| telegram | A fake Telegram Bot API the alert channel talks to, as telegram/README.md describes |
| samples | The sample videos the cameras loop, copied out of the DeepStream image and ignored by git |

The demo seed is the API module app.devkit.seed, which runs inside the API container.

## Run

From the repository root:

```
scripts/devkit.sh init
scripts/devkit.sh up
scripts/devkit.sh seed
```

init writes the gitignored deploy/devkit/.env from deploy/devkit/.env.example with generated secrets and copies the sample videos. up builds the images and starts the kit with the devkit profile; any other argument goes to docker compose, as in scripts/devkit.sh logs -f backend or scripts/devkit.sh down. Add the analytics profile with COMPOSE_PROFILES=devkit,analytics. The kit's .env turns on the devkit profile with COMPOSE_PROFILES, so docker compose run from inside deploy/devkit starts the same kit; deploy/devkit/go2rtc.yaml is the kit's go2rtc configuration, without a GPU and with WebRTC candidates for the host and the Android emulator.

| Service | Address on the host |
|---|---|
| Console | http://127.0.0.1:18750 |
| API | http://127.0.0.1:18700 |
| Mailpit | http://127.0.0.1:18725 |
| WebRTC | 127.0.0.1:18755 over TCP and UDP |
| Fake Telegram API | http://127.0.0.1:18781 |

The Android emulator reaches the same ports at 10.0.2.2, and go2rtc offers both addresses as WebRTC candidates. Postgres is only on the internal database network; tools join smart-surveillance-dev_database to reach it.

## Simulated site

| Container | Network | Address | Role |
|---|---|---|---|
| camera-gate | home LAN 172.31.250.0/24 | 172.31.250.11 | Camera with a main and a sub stream |
| camera-yard | home LAN 172.31.250.0/24 | 172.31.250.12 | PTZ camera |
| v380-camera-porch | home LAN 172.31.250.0/24 | 172.31.250.21 | V380 camera, device version 32, ADPCM audio, device id 12345678 |
| v380-camera-garage | home LAN 172.31.250.0/24 | 172.31.250.22 | V380 camera, device version 31, AAC audio, device id 23456789 |
| v380-relay-ptz-1 and v380-relay-ptz-2 | home LAN and the stack network | 172.31.250.31 and 172.31.250.32 | V380 relays that serve the two V380 cameras at /main and /sub |
| connector | home LAN and the stack network | 172.31.250.2 | Connector agent of the home site |
| camera-store | store LAN 172.31.252.0/24 | 172.31.252.11 | Camera the API reaches directly |

Both LANs are internal networks. The connector discovers only the home LAN, set by SSS_CONNECTOR_SUBNETS, and waits for pairing with its code in its log and on its pairing page. The cameras log in with SSS_DEV_CAMERA_PASSWORD. The API sends Telegram alerts to the fake Telegram API, which delivers its updates to the API's webhook; POST /fake/start on it plays a member opening a chat link.

## Demo seed

scripts/devkit.sh seed creates:

- the Demo organisation with the sites Home and Store;
- the members owner, admin, operator and viewer at <role>@SSS_DEMO_EMAIL_DOMAIN, with the password SSS_DEMO_PASSWORD from deploy/devkit/.env; the operator reaches only Home and the viewer only Store; the owner is the settings' admin, SSS_ADMIN_USERNAME=demo-owner, so after the next API start it is the platform admin and Demo is on the internal plan;
- the store camera, added by address, and the home cameras, added through the connector, which the seed pairs to Home when it waits for pairing;
- for every camera, an enabled analytics view with an Entrance zone that alerts on people and a Door line that counts people both ways, which the analytics service runs once the analytics profile is started;
- a Store switch network device, at the store LAN address of go2rtc, with the store camera linked to it, so the store's health page has a probed device; the home cameras' health comes from the connector's heartbeats;
- a week of events with snapshots taken from the cameras, and a week of minute line counts.

Running it again creates nothing that already exists.

## Test

The camera tests need no network. Run them from this folder with ruff.toml as the lint configuration:

```
cd tools/devkit/camera && python -m pytest -q
```

The V380 tests run the V380 client, adapter and relay against the simulator and need the camera_adapters package:

```
cd tools/devkit/v380 && uv run --package camera-adapters pytest -q
```

The seed's tests run with the API suite.

## Configuration

deploy/devkit/.env.example lists the variables of the kit. The camera takes its settings from CAMERA_ environment variables, listed in camera/README.md. The seed also reads DEVKIT_HOME_NAMES and DEVKIT_STORE_NAMES, comma-separated address=name pairs, and DEVKIT_STORE_SWITCH, the address of the store's stand-in switch. SSS_RATE_LIMIT_CREDENTIALS and SSS_RATE_LIMIT_MAIL are generous in the kit, because every app and test on the machine shares one client address.
