"""Service settings read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def env(name: str, default: str = "") -> str:
    """Read an environment variable.

    Args:
        name: Variable name.
        default: Value when the variable is unset or empty.

    Returns:
        The value.
    """
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Settings:
    """Everything the service reads from its environment.

    Attributes:
        rtsp_url: go2rtc RTSP server the camera streams are read from.
        rtsp_username: RTSP user go2rtc asks clients from other containers for.
        rtsp_password: RTSP password.
        timezone: Time zone of schedules.
        config_file: JSON configuration file used when no backend is set.
        backend_url: Backend base URL for configuration and events, empty to run from the file.
        backend_token: Shared token for the backend's internal analytics endpoints.
        nats_url: NATS server of the event bus that event changes and observations are published on; empty posts
            them to the backend's intake routes instead.
        models_dir: Mount point of the model folder; SSS_ANALYTICS_MODELS_DIR names the host folder compose mounts
            there.
        engines_dir: Writable engine cache.
        events_dir: Folder for event snapshots and clips, shared with the backend.
        buffer_dir: Folder of the rolling recording segments.
        detector: Name of the shared detector.
        engine_batch: Largest batch the detector engine is built for.
        workspace_mb: TensorRT builder workspace limit.
        mux_width: Stream muxer frame width.
        mux_height: Stream muxer frame height.
        tracker_config: NvDCF tracker configuration file.
        appearance: Runs person appearance and journeys on views that allow it.
        pose: Runs the pose model for fall detection on views with falls enabled.
        plates: Reads number plates on views with plates enabled.
        plate_finder: text for PaddleOCR's DB text detector, or lpdnet for NVIDIA LPDNet.
        default_fps: Analysis rate of views that set none.
        clip_encoder: auto, h264_nvenc, libx264 or copy.
        clip_before: Seconds a clip starts before its event.
        clip_after: Seconds a clip runs after its event ends.
        clip_max: Longest clip in seconds.
        buffer_seconds: Seconds of segments kept per camera.
        status_port: Port of the status endpoint.
    """

    rtsp_url: str = field(default_factory=lambda: env("SSS_ANALYTICS_RTSP_URL", "rtsp://go2rtc:8554").rstrip("/"))
    rtsp_username: str = field(default_factory=lambda: env("SSS_TV_RTSP_USERNAME", "tv"))
    rtsp_password: str = field(default_factory=lambda: env("SSS_TV_RTSP_PASSWORD"))

    timezone: str = field(default_factory=lambda: env("SSS_TIMEZONE", "UTC"))

    config_file: Path = field(default_factory=lambda: Path(env("SSS_ANALYTICS_CONFIG_FILE", "/config/analytics.json")))
    backend_url: str = field(default_factory=lambda: env("SSS_ANALYTICS_BACKEND_URL").rstrip("/"))
    backend_token: str = field(default_factory=lambda: env("SSS_ANALYTICS_TOKEN"))

    nats_url: str = field(default_factory=lambda: env("SSS_NATS_URL"))

    models_dir: Path = Path("/models")
    engines_dir: Path = field(default_factory=lambda: Path(env("SSS_ANALYTICS_ENGINES_DIR", "/engines")))
    events_dir: Path = field(default_factory=lambda: Path(env("SSS_ANALYTICS_EVENTS_DIR", "/data/events")))
    buffer_dir: Path = field(default_factory=lambda: Path(env("SSS_ANALYTICS_BUFFER_DIR", "/buffer")))

    detector: str = field(default_factory=lambda: env("SSS_ANALYTICS_DETECTOR", "rfdetr-medium"))
    engine_batch: int = field(default_factory=lambda: int(env("SSS_ANALYTICS_ENGINE_BATCH", "8")))
    workspace_mb: int = field(default_factory=lambda: int(env("SSS_ANALYTICS_WORKSPACE_MB", "1024")))

    mux_width: int = 1920
    mux_height: int = 1080
    tracker_config: Path = Path("/opt/analytics/configs/tracker.yml")

    appearance: bool = field(default_factory=lambda: env("SSS_ANALYTICS_APPEARANCE", "true").lower() == "true")
    pose: bool = field(default_factory=lambda: env("SSS_ANALYTICS_POSE", "true").lower() == "true")
    plates: bool = field(default_factory=lambda: env("SSS_ANALYTICS_PLATES", "true").lower() == "true")
    plate_finder: str = field(default_factory=lambda: env("SSS_ANALYTICS_PLATE_FINDER", "text"))

    default_fps: float = field(default_factory=lambda: float(env("SSS_ANALYTICS_FPS", "8")))

    clip_encoder: str = field(default_factory=lambda: env("SSS_ANALYTICS_CLIP_ENCODER", "auto"))
    clip_before: float = field(default_factory=lambda: float(env("SSS_ANALYTICS_CLIP_BEFORE", "5")))
    clip_after: float = field(default_factory=lambda: float(env("SSS_ANALYTICS_CLIP_AFTER", "5")))
    clip_max: float = field(default_factory=lambda: float(env("SSS_ANALYTICS_CLIP_MAX", "60")))
    buffer_seconds: float = field(default_factory=lambda: float(env("SSS_ANALYTICS_BUFFER_SECONDS", "120")))

    status_port: int = field(default_factory=lambda: int(env("SSS_ANALYTICS_STATUS_PORT", "8080")))
