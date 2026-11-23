# Smart Surveillance System - OpenCV viewer

A single window showing every camera at once, security-monitor style.
OpenCV opens the RTSP streams and decodes H.265 directly, then draws all
cameras side by side. No H.264 re-encode and no browser buffering, so it is
lighter and smoother than a web player. Software decode only; no GPU.

## Build (once)

    cd tools/cv-viewer
    docker build -t smart-surveillance-system-cv:latest .

The password comes from `deploy/server/.env` (`CAMERA_PASSWORD=...`) and the camera addresses from
`CV_CAMERA_HOSTS`, a comma-separated list, in the same file the server stack uses.

## Run the window

Open a terminal on the PC's own screen, not over SSH, and run:

    tools/cv-viewer/run.sh

A window opens with every camera in CV_CAMERA_HOSTS, sized to the screen. Keys:

- `q` or `Esc` - quit
- `f` - toggle fullscreen

Lighter mode (uses the small 768x432 sub-stream instead of 5MP):

    STREAM=sub tools/cv-viewer/run.sh

## Headless check (no window)

Saves one composed grid image and exits, for verifying access without a display, from tools/cv-viewer:

    docker run --rm --network host --env-file ../../deploy/server/.env \
      -v "$PWD":/out smart-surveillance-system-cv:latest --snapshot /out/grid.jpg

## Notes

- `run.sh` runs `xhost +local:` so the container can reach your X server.
  Undo later with `xhost -local:` if you want.
- Each capture runs in its own thread and always shows the newest frame
  (stale frames are dropped), which keeps the view live with low latency.
- The cameras are the EZVIZ addresses in `CV_CAMERA_HOSTS`, named Camera A,
  Camera B and so on in that order.
