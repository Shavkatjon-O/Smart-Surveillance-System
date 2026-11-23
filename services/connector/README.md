# Connector

The connector agent runs on a site, next to its cameras. It pairs with the backend, finds the cameras on the site's LAN, keeps their credentials, and restreams them through its own go2rtc so central reaches every camera of the site at one address. Every call it makes to the backend is outbound; central calls the agent API with the key it received at pairing.

## Run

deploy/connector runs the agent with host networking, which multicast discovery on the site LAN needs. Copy its example configuration, set SSS_CONNECTOR_BACKEND_URL to the backend's address, and start it:

```
cp deploy/connector/.env.example deploy/connector/.env
docker compose -f deploy/connector/docker-compose.yml up -d --build
docker compose -f deploy/connector/docker-compose.yml logs -f connector
```

The log shows the pairing code and its QR code. An admin of the site claims it in the app by scanning the QR code, typing the code, or picking the connector the app finds over mDNS on the same Wi-Fi. The pairing page at http://<connector>:8780/pairing shows the same code and http://<connector>:8780/pairing.svg the QR code, only while the connector waits for pairing.

## Test

Run from the repository root:

```
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache -v $PWD:/repo -w /repo/services/connector ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv sync --frozen --all-packages && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen pytest -q"
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| SSS_CONNECTOR_BACKEND_URL | http://backend:8000 | Base address of the backend |
| SSS_CONNECTOR_NAME | Connector | Name the connector suggests when pairing |
| SSS_CONNECTOR_STATE_DIR | /data | Folder of the pairing, the cameras and the store key |
| SSS_CONNECTOR_SECRET | empty | Text the store key is derived from; empty keeps a generated key file in the state folder |
| SSS_CONNECTOR_ADVERTISE_ADDRESS | empty | Address central reaches the agent at in direct mode; empty lets the backend use the heartbeat's source address |
| SSS_CONNECTOR_API_PORT | 8780 | Port of the agent API |
| SSS_CONNECTOR_RTSP_PORT | 8554 | RTSP port of the connector's go2rtc |
| SSS_CONNECTOR_GO2RTC_BINARY | /usr/local/bin/go2rtc | Path of the go2rtc program |
| SSS_CONNECTOR_GO2RTC_API | 127.0.0.1:1984 | Loopback address of go2rtc's API |
| SSS_CONNECTOR_INTERFACES | empty | Comma-separated interfaces discovery uses instead of the filter |
| SSS_CONNECTOR_SUBNETS | empty | Comma-separated subnets the sweep covers instead of the interfaces' own |
| SSS_CONNECTOR_DISCOVERY_INTERVAL | 0 | Seconds between discovery runs of its own; 0 runs discovery on request only |
| SSS_CONNECTOR_HEARTBEAT_SECONDS | 30 | Seconds between heartbeats until the backend sets its interval |
| SSS_CONNECTOR_VERIFY_TLS | true | Whether to verify the backend's certificate |
| SSS_CONNECTOR_MDNS | true | Whether to announce the pending pairing as _sss-connector._tcp |

## Discovery

Discovery sends a WS-Discovery probe with a time to live of 1 from each site LAN interface and sweeps the camera ports of those interfaces' subnets at the same time, then identifies each candidate over ONVIF without credentials. Only addresses inside the swept subnets are reported.

The site LAN filter keeps an interface only when its name is not a loopback, Docker bridge, veth, container network, Kubernetes, VPN or tunnel interface (lo, docker, br-, veth, virbr, cni, flannel, cali, cilium, kube, podman, tun, tap, wg, wt, tailscale, zt, nb-, netbird, ppp and similar prefixes) and its address is an RFC 1918 address outside Docker's default bridge network 172.17.0.0/16. SSS_CONNECTOR_INTERFACES names the interfaces explicitly and wins over the filter; otherwise SSS_CONNECTOR_SUBNETS keeps the interfaces inside the listed subnets. A sweep of a network larger than a /24 covers only the /24 around the interface address.

## Agent API

Every /api route needs the header Authorization: Bearer <agent key> and answers 503 until the connector is paired.

| Method | Path | Purpose |
|---|---|---|
| GET | /health | Liveness and whether the connector is paired; no key |
| GET | /pairing | The pending pairing code and link; no key |
| GET | /pairing.svg | The pending pairing link as a QR code; no key |
| PUT | /api/cameras/{uuid} | Store a camera with its credentials and stream it |
| DELETE | /api/cameras/{uuid} | Remove a camera and its credentials |
| POST | /api/cameras/test | Test a camera's login and find its stream paths |
| POST | /api/identify | Identify a device with the adapter registry; an adapter failure answers 200 with an error code |
| POST | /api/cameras/{uuid}/ptz/move | Start a continuous move of the camera's channel |
| POST | /api/cameras/{uuid}/ptz/stop | Stop the move |
| POST | /api/discovery | Start a discovery run that reports to the backend |

Camera credentials stay on the connector, encrypted with the store key; the camera list the backend serves carries none. The heartbeat reports each camera's reachability, and a changed config_version in the backend's answer makes the agent fetch the list and reconcile, keeping the credentials it holds.

go2rtc runs as a child of the agent with its configuration passed inline, so no file holds camera passwords. It serves RTSP only, behind the stream login central received at pairing, and names each camera's streams <uuid hex>_main and <uuid hex>_sub as central does.

## Direct and tunnel mode

The two modes differ only in the address central uses for the agent API and the RTSP port: the connector's reported or configured address in direct mode, its overlay address in tunnel mode. Direct mode works today. Tunnel mode is a contract the backend already carries:

- When pairing, the connector sends its WireGuard public key as public_key in the device code request; the backend stores it with the connector.
- The token answer carries tunnel with endpoint (central's WireGuard host and UDP port), websocket (central's wstunnel address on TCP 443, for networks without outbound UDP), public_key (central's key), address (the connector's overlay /32) and allowed_ips (the overlay addresses routed into the tunnel).
- The connector brings the interface up with wireguard-go in user space, tries UDP first and falls back to wstunnel over TCP 443, keeps the tunnel alive with a persistent keepalive, and binds the agent API and go2rtc to its overlay address as well.
- The backend switches the connector's mode to tunnel and its stored tunnel_address becomes the address central calls. Nothing else changes: the same agent key, stream login, routes and stream names apply.
- The tunnel carries only central's traffic to the agent and go2rtc; the connector never routes the site LAN into it.
