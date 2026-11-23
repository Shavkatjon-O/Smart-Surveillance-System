# Deploy

One folder per deployment target. Each folder holds one docker-compose.yml that runs on its own, the .env.example of its configuration and the files its services mount. The root README describes the product.

| Target | Compose project | Use |
|---|---|---|
| server | smart-surveillance-system | The all-in-one self-hosted site on one Linux computer with an NVIDIA GPU: API, console, go2rtc, analytics, Postgres and a bridge per V380 camera |
| central | smart-surveillance-system | The hosted multi-tenant platform: the server's services without V380 bridges, with the database password and the secret key as Docker secrets; sites join through connectors |
| connector | smart-surveillance-connector | The agent a customer runs at a site, with host networking, to pair with the platform and reach the site's cameras |
| jetson | smart-surveillance-system | A site on a Jetson Orin Nano: the server's services with RF-DETR Nano, software video encoding and no plate reading |
| devkit | smart-surveillance-dev | The development kit: the server's services with simulated sites, a fake Telegram API and Mailpit, on 127.0.0.1 only |

## Run

A target reads its configuration from .env in its own folder, which git ignores; copy the example and fill it in. Compose runs a target from inside its folder, or from the repository root with -f:

```
cp deploy/server/.env.example deploy/server/.env
cd deploy/server
docker compose up -d --build
docker compose logs -f backend
docker compose down
```

| Target | Start from the repository root |
|---|---|
| server | docker compose -f deploy/server/docker-compose.yml up -d --build |
| central | docker compose -f deploy/central/docker-compose.yml up -d --build |
| connector | docker compose -f deploy/connector/docker-compose.yml up -d --build |
| jetson | docker compose -f deploy/jetson/docker-compose.yml up -d --build |
| devkit | scripts/devkit.sh up, after scripts/devkit.sh init, as tools/devkit/README.md describes |

The data lives in named volumes of the compose project, such as smart-surveillance-system_postgres, so it survives docker compose down; never add -v.

## Profiles

| Target | Profile | Adds |
|---|---|---|
| server, jetson | v380-native | The V380 relays v380-relay-ptz-1 and v380-relay-ptz-2, which replace the bridges after the cut-over in services/v380-relay/README.md |
| devkit | devkit | The simulated cameras, the V380 simulators and relays, the connector and the fake Telegram API; the example .env turns it on with COMPOSE_PROFILES |
| devkit | analytics | The analytics service |

## Shared definitions

central, jetson and devkit take the server's service definitions with extends from ../server/docker-compose.yml and override only what differs, so a change to a service in the server reaches every target. Compose resolves the server's build contexts and bind mounts relative to deploy/server.

- Every service a target extends sets env_file to the target's own .env, because the server's .env resolves to deploy/server/.env.
- Compose interpolates the whole server file for every target that extends it, so a variable the server requires with :? is required in every target. The server requires only what every target needs, SSS_LAN_BIND and SSS_TV_RTSP_PASSWORD; the V380 camera variables default to empty and must be set on a site with V380 cameras.
- A target declares the volumes and networks its services use, because extends takes services only.

## Central secrets

SSS_SECRETS_DIR names a folder with the files sss_db_password and sss_secret_key. Postgres reads the first through POSTGRES_PASSWORD_FILE and the API reads both from /run/secrets. central/.env.example leaves out SSS_DB_PASSWORD and SSS_SECRET_KEY, because a variable set in the environment wins over its secret file.

## go2rtc

| File | Role |
|---|---|
| server/go2rtc/go2rtc.yaml | Configuration of the server and central: RTSP server, WebRTC for the apps on SSS_LAN_BIND port 8555, the h264 transcode template on h264_nvenc and the TV exec streams |
| server/go2rtc/go2rtc-tv.sh | Exec source that asks the API for the ffmpeg arguments of a TV stream and restarts when they change |
| jetson/go2rtc.yaml | The same for a Jetson, with software x264 transcoding |
| devkit/go2rtc.yaml | The kit's configuration: no GPU, WebRTC candidates for the host and the Android emulator |

Each go2rtc service mounts its go2rtc.yaml read-only at /config/go2rtc.yaml, and the server, central and jetson also mount go2rtc-tv.sh at /config/tv.sh. The API adds camera streams at runtime and never writes them to go2rtc.yaml, because go2rtc stores stream URLs with camera passwords in its config. go2rtc reads SSS_TV_RTSP_USERNAME, SSS_TV_RTSP_PASSWORD and SSS_LAN_BIND from the environment compose gives it.

## Check

Check the TV script's syntax and a target's configuration, with its .env in place:

```
sh -n deploy/server/go2rtc/go2rtc-tv.sh
docker compose -f deploy/server/docker-compose.yml config -q
```

.github/workflows/compose.yml validates every target alone, with each of its profiles and with all of them, from the example env files.

## Configuration

Each .env mirrors its .env.example group by group. The root README's configuration reference lists the server's variables, and the configuration page of the documentation site lists every target's.
