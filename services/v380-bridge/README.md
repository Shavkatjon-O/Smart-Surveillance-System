# V380 bridge

A Dockerfile that builds the pinned upstream V380 decoder, a C# project that logs in to a V380 camera over its own protocol and republishes the stream as RTSP, ONVIF and a small HTTP API. There is no source here; the image is the service. The root README describes the product.

## Run

The compose services v380-ptz-1 and v380-ptz-2 of deploy/server each build this image for one camera and pass its id, address and credentials on the command line; from deploy/server:

```
docker compose up -d --build v380-ptz-1 v380-ptz-2
```

The bridge ports stay on the stack network and are never published, because the RTSP, ONVIF and API endpoints have no authentication.

## Test

The folder has no tests. Check the build and the pinned commit:

```
docker build -t smart-surveillance-system-v380-bridge:local services/v380-bridge
```

## Configuration

| Item | Default | Meaning |
|---|---|---|
| V380_DECODER_REPO | upstream repository | Build argument: repository to clone |
| V380_DECODER_COMMIT | pinned commit | Build argument: commit of the upstream pull request that fixes short login replies |
| V380_USERNAME | admin | Camera login (compose) |
| V380_PASSWORD | required | Camera password (compose) |
| V380_PTZ_1_HOST, V380_PTZ_1_ID | required | Address and id of the first camera (compose) |
| V380_PTZ_2_HOST, V380_PTZ_2_ID | required | Address and id of the second camera (compose) |
