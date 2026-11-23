"""The analytics service: configuration, pipeline supervision, rules, tamper guard, faces, features, media and status.

The DeepStream pipeline runs in a child process and sends tracked objects back over a queue. Everything else runs
here: the rules engines, the tamper watch, the recorders, clip and snapshot cutting, and the status endpoint. Rule
and zone changes apply at once; only a change to what the pipeline decodes or analyses restarts it.
"""

from __future__ import annotations

import collections
import json
import logging
import multiprocessing
import queue
import statistics
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from app.backend import BackendClient, BackendConfigSource, Outbox
from app.config import ServiceConfig, ViewConfig, parse_config
from app.detectors import detector
from app.features import FeatureHub
from app.identity import enroll
from app.identity.models import FaceModels
from app.identity.settings import IdentitySettings
from app.identity.stage import FaceStage, GallerySync, ViewInfo
from app.pipeline.plan import PipelinePlan, SourcePlan, ViewPlan
from app.plates.ctc import load_characters
from app.plates.detect import LpdFinder
from app.plates.frames import picture_at
from app.plates.reader import LPD_FINDER, PlateReader, PlateSettings
from app.plates.textboxes import TextFinder
from app.plates.watch import PlateJob
from app.recorder import CameraRecorder, ClipMaker, latest_frame
from app.rules.engine import Event, RulesEngine, Update
from app.rules.model import ArmMode, Box, Detection, EventKind, Frame, ViewRules
from app.scene import SceneMatcher
from app.settings import Settings
from app.sink import EventSink
from app.sources import probe_size, redact, stream_uri
from app.stages import engines
from app.stages.models import PLATE_DETECTOR, PLATE_DICTIONARY, PLATE_DICTIONARY_SHA256, PLATE_READER, PLATE_TEXT
from app.stages.probe import PersonStages
from app.status import StatusServer
from app.tamper import lens_band
from app.tampering.guard import GuardSettings, TamperGuard

logger = logging.getLogger(__name__)

LOOP_SECONDS = 1.0
CONFIG_POLL_SECONDS = 5.0
STALL_SECONDS = 20.0
PIPELINE_STALL_SECONDS = 90.0
RESTART_DELAYS = (5, 10, 30, 60, 120)
SNAPSHOT_DELAY = 1.5
SNAPSHOT_RETRIES = (3.0, 6.0, 12.0)
RECENT_EVENTS = 200
GPU_POLL_SECONDS = 10.0
OBSERVATION_SECONDS = 5.0
REFERENCE_PRUNE_SECONDS = 3600.0
REFERENCE_KEEP_SECONDS = 86400.0
MEDIA_EVENT_LIMIT = 500


class FileConfigSource:
    """Reads the configuration from a JSON file whenever the file changes.

    Attributes:
        path: The file.
        mtime: Modification time of the last read.
    """

    def __init__(self, path: Path):
        """Watch a file.

        Args:
            path: The JSON file.
        """
        self.path = path
        self.mtime: float | None = None

    def poll(self) -> dict | None:
        """Return the configuration when the file changed since the last call.

        Returns:
            The parsed JSON, or None when unchanged or unreadable.
        """
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return None
        if mtime == self.mtime:
            return None
        self.mtime = mtime
        return json.loads(self.path.read_text())


@dataclass
class ViewRuntime:
    """Live state of one view.

    Attributes:
        config: The view's configuration.
        engine: Its rules engine, None for views without rules.
        suspended: Zones and lines suspended because the moving lens left the scene they were drawn on.
        tamper: Its tamper guard, None when off.
        frames: Frames analysed since the service started.
        stamps: Arrival times of recent frames, for the rate.
        latencies: Recent pipeline latencies in milliseconds.
        last_frame_at: Arrival time of the latest frame.
        objects: Tracked objects of the latest frame.
        events: Events produced.
        errors: Recent problems.
    """

    config: ViewConfig
    engine: RulesEngine | None = None
    suspended: frozenset[str] = frozenset()
    tamper: TamperGuard | None = None
    frames: int = 0
    stamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=300))
    latencies: collections.deque = field(default_factory=lambda: collections.deque(maxlen=200))
    last_frame_at: float | None = None
    objects: list[dict] = field(default_factory=list)
    events: int = 0
    errors: collections.deque = field(default_factory=lambda: collections.deque(maxlen=10))

    def fps(self, now: float, window: float = 10.0) -> float:
        """Return the analysed frame rate over a recent window.

        Args:
            now: Current time.
            window: Window length in seconds.

        Returns:
            Frames per second.
        """
        recent = [stamp for stamp in self.stamps if now - stamp <= window]
        return round(len(recent) / window, 2)

    def latency(self) -> dict:
        """Return pipeline latency percentiles.

        Returns:
            Median and 95th percentile in milliseconds, empty without samples.
        """
        if not self.latencies:
            return {}
        ordered = sorted(self.latencies)
        return {
            "p50_ms": round(statistics.median(ordered), 1),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
        }


@dataclass
class PendingClip:
    """A clip waiting for its event to end or its length cap.

    Attributes:
        event: The event.
        camera: Camera id.
        lens: Lens band.
        lenses: Bands in the frame.
        start: Clip start.
        deadline: Time at which the clip is cut even if the event is still open.
    """

    event: dict
    camera: str
    lens: int
    lenses: int
    start: float
    deadline: float


class Service:
    """Runs everything except the pipeline itself.

    Attributes:
        settings: Environment settings.
        timezone: Time zone of schedules.
        backend: Client of the backend's internal routes, None when running from a file.
        outbox_backend: Queue of reports to the backend, None when running from a file.
        sink: Where event changes and observations go: the event bus, or the backend's intake routes.
        source: Where the configuration comes from, the backend or a JSON file.
        scenes: Matcher of moving-lens pictures against zone reference pictures.
        identity: Face recognition settings.
        faces: The face stage: embedding, matching and clustering.
        gallery_sync: Keeps the face gallery in step with the backend, None without a backend.
        faces_note: Why faces are off, if they are.
        media_events: Media paths written for recent events, by event id.
        observed: Time observations were last sent.
        references_pruned: Time unused zone reference pictures were last pruned.
        config: Current configuration.
        views: Runtime state per view id.
        recorders: Recording buffer per camera id.
        sizes: Frame size per camera id.
        lock: Guards the views, the pending clips and the configuration.
        started_at: Time the service started.
        context: Multiprocessing context the pipeline process starts from.
        process: The pipeline process, if running.
        outbox: Queue the pipeline process sends its messages on.
        plan: Plan of the running pipeline.
        plan_key: What the plan depends on, to tell when the pipeline must restart.
        pipeline_state: State the pipeline last reported.
        pipeline_message: Last message about the pipeline.
        pipeline_started: Time the pipeline last started.
        pipeline_restarts: Restarts since the service started.
        pipeline_failures: Failures in a row, for the restart backoff.
        retry_at: Earliest time of the next restart.
        dropped_batches: Frame batches dropped because the queue was full.
        clips: Cuts snapshots and clips out of the buffers.
        media_jobs: Snapshots waiting to be cut.
        pending: Clips waiting for their events to end, by event id.
        recent: Recent event changes for the status endpoint.
        tamper_events: Open tamper events by view id and subtype.
        gpu: Latest GPU reading.
        gpu_polled: Time of that reading.
        stopping: Set when the service should stop.
        person_views: View of each muxer pad that feeds the person stages.
        persons_note: Why appearance and pose are off, if they are.
        plates_note: Why plate reading is off, if it is.
        plate_reader_built: True once building the plate reader was tried.
        plate_reader_instance: The plate reader, None until built or when it cannot be built.
        features: Hub of tamper, counts, objects, falls, follow, plates and journeys.
    """

    def __init__(self, settings: Settings):
        """Create the service.

        Args:
            settings: Environment settings.
        """
        self.settings = settings
        self.timezone = ZoneInfo(settings.timezone)
        self.backend: BackendClient | None = None
        self.outbox_backend: Outbox | None = None
        if settings.backend_url:
            self.backend = BackendClient(settings.backend_url, settings.backend_token)
            self.outbox_backend = Outbox(self.backend)
            self.source: FileConfigSource | BackendConfigSource = BackendConfigSource(self.backend)
        else:
            self.source = FileConfigSource(settings.config_file)
        self.sink = EventSink(settings.nats_url, self.outbox_backend)
        self.scenes = SceneMatcher(settings.events_dir)
        self.identity = IdentitySettings()
        self.faces = FaceStage(
            self.identity,
            FaceModels(self.identity, settings.engines_dir, settings.workspace_mb),
            settings.events_dir,
            self.publish_face,
            lambda: self.arm_mode().value,
            self.related_event,
        )
        self.gallery_sync: GallerySync | None = None
        if self.backend is not None:
            client = self.backend
            self.gallery_sync = GallerySync(lambda: client.request("GET", "/internal/people/gallery"), self.faces)
        self.faces_note: str | None = None
        self.media_events: collections.OrderedDict[str, dict] = collections.OrderedDict()
        self.observed = 0.0
        self.references_pruned = 0.0
        self.config: ServiceConfig | None = None
        self.views: dict[str, ViewRuntime] = {}
        self.recorders: dict[str, CameraRecorder] = {}
        self.sizes: dict[str, tuple[int, int]] = {}
        self.lock = threading.RLock()
        self.started_at = time.time()

        self.context = multiprocessing.get_context("spawn")
        self.process: multiprocessing.Process | None = None
        self.outbox: multiprocessing.Queue | None = None
        self.plan: PipelinePlan | None = None
        self.plan_key: tuple | None = None
        self.pipeline_state = "idle"
        self.pipeline_message = ""
        self.pipeline_started: float | None = None
        self.pipeline_restarts = 0
        self.pipeline_failures = 0
        self.retry_at = 0.0
        self.dropped_batches = 0

        self.clips = ClipMaker(settings.clip_encoder)
        self.media_jobs: queue.Queue = queue.Queue()
        self.pending: dict[str, PendingClip] = {}
        self.recent: collections.deque = collections.deque(maxlen=RECENT_EVENTS)
        self.tamper_events: dict[tuple[str, str], Event] = {}
        self.gpu: dict = {}
        self.gpu_polled = 0.0
        self.stopping = threading.Event()

        self.person_views: dict[int, ViewConfig] = {}
        self.persons_note: str | None = None
        self.plates_note: str | None = None
        self.plate_reader_built = False
        self.plate_reader_instance: PlateReader | None = None
        self.features = FeatureHub(
            self.timezone,
            self.arm_mode,
            self.handle,
            self.outbox_backend.send if self.outbox_backend is not None else None,
            self.attach_details,
            self.plate_reader,
            self.plate_picture,
            state_dir=settings.engines_dir,
            backend=(settings.backend_url, settings.backend_token) if settings.backend_url else None,
            appearance=settings.appearance,
        )

    def arm_mode(self) -> ArmMode:
        """Return the current arm mode.

        Returns:
            The arm mode of the configuration, disarmed without one.
        """
        return self.config.arm_mode if self.config else ArmMode.DISARMED

    def run(self) -> None:
        """Run until stopped."""
        StatusServer(
            self.settings.status_port,
            self.settings.backend_token,
            self.status,
            self.live_objects,
            self.view_snapshot,
            lambda data: enroll.answer(data, self.faces.models, self.identity),
        ).start()
        self.faces.start()
        self.features.start()
        if self.outbox_backend is not None:
            self.outbox_backend.start()
        self.sink.start()
        threading.Thread(target=self.media_loop, name="media", daemon=True).start()
        threading.Thread(target=self.read_loop, name="reader", daemon=True).start()
        polled = 0.0
        while not self.stopping.is_set():
            now = time.time()
            if now - polled >= CONFIG_POLL_SECONDS:
                polled = now
                self.reload()
            try:
                self.supervise(now)
                self.tick(now)
                self.features.tick(now)
                self.maintain(now)
                self.observe(now)
            except Exception:
                logger.exception("Service loop step failed")
            self.stopping.wait(LOOP_SECONDS)
        self.shutdown()

    def shutdown(self) -> None:
        """Stop the features, the pipeline and the recorders."""
        self.features.stop()
        self.stop_pipeline()
        for recorder in self.recorders.values():
            recorder.stop()

    def reload(self) -> None:
        """Apply a changed configuration."""
        try:
            data = self.source.poll()
        except (OSError, ValueError) as exc:
            logger.warning("Reading the configuration failed: %s", exc)
            return
        if data is None:
            return
        try:
            config = parse_config(data, self.settings.default_fps)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("The configuration is invalid and was not applied: %s", exc)
            return
        self.apply(config)

    def apply(self, config: ServiceConfig) -> None:
        """Make the runtime match a configuration.

        Args:
            config: The new configuration.
        """
        with self.lock:
            self.config = config
            now = time.time()
            wanted = {view.id: view for view in config.views}
            for view_id in [key for key in self.views if key not in wanted]:
                self.close_view(view_id, now)
                del self.views[view_id]
            for view in config.views:
                runtime = self.views.get(view.id)
                if runtime is None:
                    runtime = self.views[view.id] = ViewRuntime(view)
                runtime.config = view
                if view.enabled and view.profile.rules:
                    rules = self.effective_rules(view, runtime.suspended)
                    if runtime.engine is None:
                        runtime.engine = RulesEngine(
                            view.id, rules, now, self.timezone, self.arm_mode, self.crossing_hook(view.id)
                        )
                    else:
                        self.handle(view, runtime.engine.configure(rules))
                elif runtime.engine is not None:
                    self.handle(view, runtime.engine.configure(type(view.rules)()))
                    runtime.engine = None
                tamper = view.enabled and bool(view.raw.get("tamper", view.profile.tamper))
                moving = bool(view.raw.get("moving", False))
                guard = GuardSettings.from_dict(view.raw.get("tamper_settings"))
                if tamper and (
                    runtime.tamper is None or (runtime.tamper.moving, runtime.tamper.settings) != (moving, guard)
                ):
                    runtime.tamper = TamperGuard(guard, moving=moving)
                elif not tamper:
                    runtime.tamper = None
            self.features.apply(config, now)
        self.sync_recorders()
        logger.info(
            "Configuration %s applied: %d cameras, %d views, %d analysed, arm mode %s",
            config.version,
            len(config.cameras),
            len(config.views),
            sum(1 for view in config.views if view.detects),
            config.arm_mode.value,
        )

    def crossing_hook(self, view_id: str):
        """Return the callback a view's rules engine calls for every counted line crossing.

        Args:
            view_id: The view.

        Returns:
            A callable taking the line id, direction, label and time.
        """

        def counted(line: str, direction: str, label: str, at: float) -> None:
            """Hand a counted crossing to the feature hub.

            Args:
                line: Line id.
                direction: in or out.
                label: Object label.
                at: Time of the crossing.
            """
            self.features.crossing(view_id, line, direction, label, at)

        return counted

    @staticmethod
    def references(view: ViewConfig) -> set[str]:
        """Return the reference snapshots of a view's zones and lines that can be suspended.

        Args:
            view: The view.

        Returns:
            Reference paths; whole-view zones never depend on one.
        """
        zones = {zone.reference for zone in view.rules.zones if zone.reference and not zone.whole_view}
        return zones | {line.reference for line in view.rules.lines if line.reference}

    def effective_rules(self, view: ViewConfig, suspended: frozenset[str]) -> ViewRules:
        """Return a view's rules with the zones and lines whose scene is gone marked suspended.

        Args:
            view: The view.
            suspended: Ids of the zones and lines to suspend.

        Returns:
            The rules to run.
        """
        if not suspended:
            return view.rules
        zones = tuple(replace(zone, suspended=True) if zone.id in suspended else zone for zone in view.rules.zones)
        lines = tuple(replace(line, suspended=True) if line.id in suspended else line for line in view.rules.lines)
        return replace(view.rules, zones=zones, lines=lines)

    def rescan(self, runtime: ViewRuntime) -> None:
        """Recompute which zones and lines of a view are suspended and apply a change.

        Args:
            runtime: The view.
        """
        view = runtime.config
        suspended = set()
        for item in (*view.rules.zones, *view.rules.lines):
            whole = getattr(item, "whole_view", False)
            if item.reference and not whole and not self.scenes.matched(view.id, item.reference):
                suspended.add(item.id)
        frozen = frozenset(suspended)
        if frozen == runtime.suspended:
            return
        runtime.suspended = frozen
        if runtime.engine is not None:
            self.handle(view, runtime.engine.configure(self.effective_rules(view, frozen)))

    def close_view(self, view_id: str, now: float) -> None:
        """Close the open events of a view that goes away.

        Args:
            view_id: The view.
            now: Current time.
        """
        runtime = self.views.get(view_id)
        if runtime and runtime.engine:
            closing = [runtime.engine.close(key, now) for key in list(runtime.engine.episodes)]
            self.handle(runtime.config, closing)

    def uri(self, stream: str) -> str:
        """Return the go2rtc URI of a stream.

        Args:
            stream: Stream name.

        Returns:
            The URI with credentials.
        """
        settings = self.settings
        return stream_uri(settings.rtsp_url, stream, settings.rtsp_username, settings.rtsp_password)

    def sync_recorders(self) -> None:
        """Run one recorder per camera that has an enabled view."""
        if self.config is None:
            return
        wanted = {camera.id: camera for camera in self.config.cameras if any(view.enabled for view in camera.views)}
        for camera_id in [key for key in self.recorders if key not in wanted]:
            self.recorders.pop(camera_id).stop()
        for camera in wanted.values():
            recorder = self.recorders.get(camera.id)
            uri = self.uri(camera.stream)
            if recorder and (recorder.uri != uri or recorder.lenses != camera.lenses):
                self.recorders.pop(camera.id).stop()
                recorder = None
            if recorder is None:
                recorder = CameraRecorder(
                    camera.id,
                    uri,
                    camera.lenses,
                    self.settings.buffer_dir / camera.id,
                    self.settings.buffer_seconds,
                    self.on_keyframe,
                )
                self.recorders[camera.id] = recorder
                recorder.start()

    def build_plan(self) -> tuple[PipelinePlan | None, tuple | None]:
        """Work out the pipeline the configuration needs.

        Returns:
            The plan and its key, or None and None when nothing needs detection.
        """
        if self.config is None:
            return None, None
        sources = []
        faces: list[int] = []
        appearance: list[int] = []
        poses: list[int] = []
        index = 0
        for camera in self.config.cameras:
            views = [view for view in camera.views if view.detects]
            if not views:
                continue
            uri = self.uri(camera.stream)
            width, height = 0, 0
            if any(view.lens for view in views):
                if camera.id not in self.sizes:
                    size = probe_size(uri)
                    if size is None:
                        logger.warning("Camera %s did not answer, its lens views wait for the next try", camera.name)
                        continue
                    self.sizes[camera.id] = size
                width, height = self.sizes[camera.id]
            plans = []
            for view in views:
                plans.append(ViewPlan(index, view.id, view.lens, camera.lenses, view.fps))
                if view.recognises:
                    faces.append(index)
                if view.appearance and self.settings.appearance:
                    appearance.append(index)
                if view.falls and self.settings.pose:
                    poses.append(index)
                index += 1
            sources.append(SourcePlan(camera.id, uri, width, height, tuple(plans)))
        if not sources:
            return None, None
        item = detector(self.settings.detector)
        plan = PipelinePlan(
            sources=tuple(sources),
            detector=item.name,
            models_dir=str(self.settings.models_dir),
            engines_dir=str(self.settings.engines_dir),
            workspace_mb=self.settings.workspace_mb,
            threshold=0.4,
            tracker_config=str(self.settings.tracker_config),
            labels=dict(item.classes),
            width=self.settings.mux_width,
            height=self.settings.mux_height,
            engine_batch=self.settings.engine_batch,
            faces=tuple(faces),
            persons=(
                PersonStages(
                    frozenset(appearance),
                    frozenset(poses),
                    str(self.settings.models_dir),
                    str(self.settings.engines_dir),
                    self.settings.workspace_mb,
                )
                if appearance or poses
                else None
            ),
        )
        key = (self.config.pipeline_key(), tuple(sorted(self.sizes.items())))
        return plan, key

    def supervise(self, now: float) -> None:
        """Start, restart or stop the pipeline process as the configuration and its health require.

        Args:
            now: Current time.
        """
        if self.config is None:
            return
        key = self.config.pipeline_key()
        alive = self.process is not None and self.process.is_alive()
        if alive and self.plan_key is not None and self.plan_key[0] != key:
            logger.info("The analysed views changed, restarting the pipeline")
            self.stop_pipeline()
            alive = False
            self.retry_at = 0.0
        if alive and self.pipeline_started and now - self.pipeline_started > PIPELINE_STALL_SECONDS:
            last = max((runtime.last_frame_at or 0.0 for runtime in self.views.values()), default=0.0)
            if self.pipeline_state == "running" and now - last > PIPELINE_STALL_SECONDS:
                logger.warning("No view delivered frames for %.0f s, restarting the pipeline", now - last)
                self.stop_pipeline()
                alive = False
        if alive:
            return
        if self.process is not None:
            code = self.process.exitcode
            self.process = None
            self.pipeline_failures += 1
            delay = RESTART_DELAYS[min(self.pipeline_failures - 1, len(RESTART_DELAYS) - 1)]
            self.retry_at = now + delay
            self.pipeline_state = "restarting"
            self.pipeline_message = f"The pipeline exited with code {code}, retrying in {delay} s"
            logger.warning(self.pipeline_message)
            return
        if now < self.retry_at:
            return
        plan, plan_key = self.build_plan()
        if plan is None:
            self.pipeline_state = "idle"
            self.plan, self.plan_key = None, None
            return
        self.start_pipeline(plan, plan_key)

    def start_pipeline(self, plan: PipelinePlan, key: tuple) -> None:
        """Start the pipeline process.

        Args:
            plan: What to run.
            key: The plan key, compared later to detect changes.
        """
        from app.pipeline.worker import run

        self.outbox = self.context.Queue(maxsize=200)
        self.process = self.context.Process(target=run, args=(plan, self.outbox), name="pipeline", daemon=True)
        self.process.start()
        configs = {view.id: view for view in self.config.views} if self.config else {}
        face_views = {}
        for view in plan.views:
            config = configs.get(view.view)
            if view.index in plan.faces and config is not None:
                face_views[view.index] = ViewInfo(config.id, config.name, config.camera)
        self.faces.set_views(face_views)
        self.faces_note = None
        self.person_views = {view.index: configs[view.view] for view in plan.views if view.view in configs}
        self.persons_note = None
        self.plan, self.plan_key = plan, key
        self.pipeline_started = time.time()
        self.pipeline_restarts += 1
        self.pipeline_state = "starting"
        self.pipeline_message = f"Starting {len(plan.views)} views on {len(plan.sources)} cameras"
        logger.info(self.pipeline_message)

    def stop_pipeline(self) -> None:
        """Stop the pipeline process."""
        process, self.process = self.process, None
        if process is None:
            return
        process.terminate()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join(5)
        self.pipeline_state = "stopped"

    def read_loop(self) -> None:
        """Consume messages from the pipeline process."""
        while not self.stopping.is_set():
            outbox = self.outbox
            if outbox is None:
                time.sleep(0.2)
                continue
            try:
                message = outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            except (OSError, EOFError, ValueError):
                time.sleep(0.2)
                continue
            try:
                self.on_message(message)
            except Exception:
                logger.exception("Handling a pipeline message failed")

    def on_message(self, message: tuple) -> None:
        """Handle one message from the pipeline process.

        Args:
            message: A tuple whose first item names its kind.
        """
        kind = message[0]
        if kind == "frames":
            _, received, frames, dropped = message
            self.dropped_batches = dropped
            if self.pipeline_state != "running":
                self.pipeline_state = "running"
                self.pipeline_failures = 0
                self.pipeline_message = "Running"
                logger.info("The pipeline delivers frames")
            self.on_frames(received, frames)
        elif kind in ("failed", "eos", "stopped"):
            self.pipeline_message = message[2]
            logger.warning("Pipeline: %s", message[2])
        elif kind == "faces":
            self.faces.submit(message[2])
        elif kind == "faces_off":
            self.faces_note = message[2]
            logger.warning("Pipeline: %s", message[2])
        elif kind == "persons":
            self.features.on_persons(self.person_views, message[2], message[3])
        elif kind == "persons_off":
            self.persons_note = message[2]
            logger.warning("Pipeline: %s", message[2])
        elif kind in ("starting", "preparing"):
            self.pipeline_message = str(message[2])
            logger.info("Pipeline: %s", message[2])

    def on_frames(self, received: float, frames: list) -> None:
        """Run the rules on a batch of tracked frames.

        Args:
            received: Time the batch left the tracker.
            frames: Tuples of pad index, frame number, mux time in nanoseconds and objects.
        """
        plan = self.plan
        if plan is None:
            return
        views = plan.views
        for index, frame_number, ntp, objects in frames:
            if index >= len(views):
                continue
            with self.lock:
                runtime = self.views.get(views[index].view)
                if runtime is None:
                    continue
                profile = runtime.config.profile
                runtime.frames += 1
                runtime.stamps.append(received)
                runtime.last_frame_at = received
                if ntp:
                    runtime.latencies.append(max(0.0, (received - ntp / 1e9) * 1000))
                everything = tuple(
                    Detection(track, label, max(found, kept), Box(left, top, right, bottom), found > 0)
                    for track, label, found, kept, left, top, right, bottom in objects
                )
                detections = tuple(item for item in everything if item.label in profile.labels)
                runtime.objects = [
                    {
                        "track": item.track_id,
                        "label": item.label,
                        "confidence": item.confidence,
                        "box": item.box.rounded(),
                    }
                    for item in detections
                ]
                if runtime.engine is not None:
                    self.handle(runtime.config, runtime.engine.process(Frame(received, detections)))
                self.features.on_frame(runtime.config, received, everything, runtime.engine)

    def tick(self, now: float) -> None:
        """Close episodes of views whose frames stopped.

        Args:
            now: Current time.
        """
        with self.lock:
            for runtime in self.views.values():
                if runtime.engine is not None:
                    self.handle(runtime.config, runtime.engine.tick(now))

    def handle(self, view: ViewConfig, updates: list[Update]) -> None:
        """Record event changes and schedule their media.

        Args:
            view: The view the events belong to.
            updates: Event changes from a rules engine, the tamper watch or the feature hub.
        """
        for update in updates:
            self.features.on_update(update)
            event = update.event.as_dict()
            event.update({"camera": view.camera, "view_name": view.name, "profile": view.profile.name})
            event["action"] = update.action
            runtime = self.views.get(view.id)
            if update.action in ("open", "instant"):
                if runtime:
                    runtime.events += 1
                self.media_jobs.put(("snapshot", update.event.started_at + SNAPSHOT_DELAY, event, view))
                start = update.event.started_at - self.settings.clip_before
                self.pending[event["id"]] = PendingClip(
                    event, view.camera, view.lens, view.lenses, start, start + self.settings.clip_max
                )
            pending = self.pending.get(event["id"]) if update.action in ("close", "instant") else None
            if pending is not None:
                pending.event = event
                ended = event["ended_at"] or update.event.started_at
                pending.deadline = min(pending.deadline, ended + self.settings.clip_after)
            self.publish(event)

    def owner(self, view_id: str) -> tuple[str | None, str | None]:
        """Return the organisation and site of a view, from the configuration the backend gave it.

        Args:
            view_id: The view.

        Returns:
            Organisation and site ids, None where the configuration names none.
        """
        runtime = self.views.get(view_id)
        raw = runtime.config.raw if runtime is not None else {}
        return raw.get("organisation"), raw.get("site")

    def publish(self, event: dict) -> None:
        """Log an event change, keep it for the status endpoint and send it to the bus.

        Args:
            event: The event with its action.
        """
        known = self.media_events.get(event["id"], {})
        event = {
            **event,
            **{key: known[key] for key in ("snapshot_path", "thumbnail_path", "clip_path") if key in known},
        }
        self.media_events[event["id"]] = event
        while len(self.media_events) > MEDIA_EVENT_LIMIT:
            self.media_events.popitem(last=False)
        self.sink.event(event, self.owner(str(event["view"])))
        self.recent.append(event)
        logger.info(
            "Event %s %s %s %s%s%s objects=%s alert=%s",
            event["action"],
            event["kind"],
            event["label"],
            event["view_name"],
            f" zone={event['zone_name']}" if event.get("zone_name") else "",
            f" line={event['line_name']} {event['direction']}" if event.get("line_name") else "",
            event["objects"],
            event["alert"],
        )

    def media_paths(self, event: dict) -> tuple[Path, str]:
        """Return the folder and relative stem of an event's media.

        Args:
            event: The event.

        Returns:
            The absolute folder and the stem relative to the events folder.
        """
        started = time.gmtime(event["started_at"])
        relative = f"analytics/{started.tm_year:04d}/{started.tm_mon:02d}/{started.tm_mday:02d}/{event['id']}"
        return self.settings.events_dir, relative

    def media_loop(self) -> None:
        """Cut snapshots when due and clips when their events end or reach the length cap."""
        waiting: list[tuple[float, dict, ViewConfig, int]] = []
        while not self.stopping.is_set():
            try:
                while True:
                    kind, due, event, view = self.media_jobs.get_nowait()
                    waiting.append((due, event, view, 0))
            except queue.Empty:
                pass
            now = time.time()
            for job in [job for job in waiting if job[0] <= now]:
                waiting.remove(job)
                due, event, view, attempt = job
                if self.cut_snapshot(event, view):
                    continue
                if attempt < len(SNAPSHOT_RETRIES):
                    waiting.append((now + SNAPSHOT_RETRIES[attempt], event, view, attempt + 1))
                else:
                    logger.warning("Snapshot of event %s gave up: the buffer has no picture of its time", event["id"])
            with self.lock:
                ended = [event_id for event_id, item in self.pending.items() if now >= item.deadline]
                due = [self.pending.pop(event_id) for event_id in ended]
            for pending in due:
                self.cut_clip(pending)
            self.stopping.wait(0.5)

    def cut_snapshot(self, event: dict, view: ViewConfig) -> bool:
        """Write the snapshot of an event.

        Args:
            event: The event.
            view: Its view.

        Returns:
            True when the snapshot was written or cannot be written at all; False to try again later.
        """
        root, stem = self.media_paths(event)
        folder = self.settings.buffer_dir / view.camera
        target, thumbnail = root / f"{stem}.jpg", root / f"{stem}.thumb.jpg"
        recorder = self.recorders.get(view.camera)
        seen = recorder.seen if recorder else None
        try:
            ok = self.clips.snapshot(folder, event["started_at"], view.lens, view.lenses, target, thumbnail, seen)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Snapshot of event %s failed: %s", event["id"], exc)
            return True
        if not ok:
            return False
        logger.info("Snapshot of event %s written to %s.jpg", event["id"], stem)
        self.attach_media(event["id"], snapshot_path=f"{stem}.jpg", thumbnail_path=f"{stem}.thumb.jpg")
        return True

    def cut_clip(self, pending: PendingClip) -> None:
        """Write the clip of an event.

        Args:
            pending: The pending clip.
        """
        root, stem = self.media_paths(pending.event)
        folder = self.settings.buffer_dir / pending.camera
        target = root / f"{stem}.mp4"
        started = time.monotonic()
        recorder = self.recorders.get(pending.camera)
        seen = recorder.seen if recorder else None
        try:
            ok = self.clips.clip(folder, pending.start, pending.deadline, pending.lens, pending.lenses, target, seen)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Clip of event %s failed: %s", pending.event["id"], exc)
            return
        if ok:
            logger.info(
                "Clip of event %s written to %s.mp4 with %s in %.1f s",
                pending.event["id"],
                stem,
                self.clips.working,
                time.monotonic() - started,
            )
            self.attach_media(pending.event["id"], clip_path=f"{stem}.mp4")

    def attach_media(self, event_id: str, **paths: str) -> None:
        """Record media written for an event and send the event again with their paths.

        Args:
            event_id: The event.
            **paths: Media paths relative to the events folder.
        """
        with self.lock:
            event = self.media_events.get(event_id)
            if event is None:
                return
            self.media_events[event_id] = {**event, **paths}
            self.publish({**event, **paths, "action": "media"})

    def attach_details(self, event_id: str, details: dict) -> None:
        """Add details such as plate candidates to a published event and send it again.

        Args:
            event_id: The event.
            details: Fields to add.
        """
        with self.lock:
            event = self.media_events.get(event_id)
            if event is None:
                return
            self.media_events[event_id] = {**event, **details}
            self.publish({**event, **details, "action": "update"})

    def plate_reader(self) -> PlateReader | None:
        """Return the plate reader, loading its engines on first use.

        Returns:
            The reader, or None when plates are off or the models are missing.
        """
        if self.plate_reader_built or not self.settings.plates:
            return self.plate_reader_instance
        self.plate_reader_built = True
        from app.identity.runtime import Cuda, EngineRunner

        settings = self.settings
        try:
            cuda = Cuda()
            finder_model = PLATE_DETECTOR if settings.plate_finder == LPD_FINDER else PLATE_TEXT
            finder_engine = engines.ensure(
                finder_model, settings.models_dir, settings.engines_dir, settings.workspace_mb
            )
            finder_runner = EngineRunner(finder_engine, cuda)
            finder = LpdFinder(finder_runner) if settings.plate_finder == LPD_FINDER else TextFinder(finder_runner)
            reader_engine = engines.ensure(
                PLATE_READER, settings.models_dir, settings.engines_dir, settings.workspace_mb
            )
            dictionary = PLATE_READER.path(settings.models_dir).parent / PLATE_DICTIONARY
            engines.verify(dictionary, PLATE_DICTIONARY_SHA256)
            self.plate_reader_instance = PlateReader(
                finder, EngineRunner(reader_engine, cuda), load_characters(dictionary), PlateSettings()
            )
            logger.info("Plate reading is on with the %s finder", finder.kind)
        except (OSError, ValueError, RuntimeError) as exc:
            self.plates_note = f"Plates are off: {exc}"
            logger.warning(self.plates_note)
        return self.plate_reader_instance

    def plate_picture(self, view: ViewConfig, job: PlateJob) -> np.ndarray | None:
        """Cut the full-resolution picture of a plate job from the camera's buffer.

        Args:
            view: The view of the job.
            job: The job.

        Returns:
            RGB pixels, or None when the buffer does not hold the moment.
        """
        recorder = self.recorders.get(view.camera)
        seen = recorder.seen if recorder else None
        folder = self.settings.buffer_dir / view.camera
        return picture_at(folder, job.at, view.lens, view.lenses, job.box.as_tuple(), seen)

    def on_keyframe(self, camera: str, at: float, data: bytes) -> None:
        """Feed a keyframe thumbnail to the tamper guard and the background fallback of the camera's views.

        Args:
            camera: Camera id.
            at: Time of the keyframe.
            data: Grey thumbnail bytes.
        """
        recorder = self.recorders.get(camera)
        if recorder is None:
            return
        width, height = recorder.thumb_size
        image = np.frombuffer(data, dtype=np.uint8).reshape(height, width)
        with self.lock:
            for runtime in self.views.values():
                view = runtime.config
                if view.camera != camera or not view.enabled:
                    continue
                band = lens_band(image, view.lens, view.lenses)
                references = self.references(view)
                if references and self.scenes.update(view.id, band, references):
                    self.rescan(runtime)
                self.features.on_keyframe(view, band, at, [Box(*item["box"]) for item in runtime.objects])
                if runtime.tamper is None:
                    continue
                started, ended = runtime.tamper.update(band, at)
                updates = []
                for condition in started:
                    event = Event(
                        id=str(uuid.uuid4()),
                        view=view.id,
                        kind=EventKind.TAMPER,
                        label=condition,
                        started_at=at,
                        count=1,
                        alert=self.arm_mode() != ArmMode.DISARMED,
                    )
                    self.tamper_events[(view.id, condition)] = event
                    updates.append(Update("open", event))
                for condition in ended:
                    event = self.tamper_events.pop((view.id, condition), None)
                    if event is not None:
                        event.ended_at = at
                        updates.append(Update("close", event))
                self.handle(view, updates)

    def maintain(self, now: float) -> None:
        """Prune the buffers, refresh the GPU reading and keep the face gallery current.

        Args:
            now: Current time.
        """
        if self.gallery_sync is not None:
            self.gallery_sync.poll(now)
        with self.lock:
            pending = list(self.pending.values())
        for camera_id, recorder in list(self.recorders.items()):
            starts = [item.start for item in pending if item.camera == camera_id]
            recorder.prune(min(starts) if starts else None)
            recorder.check()
        if now - self.gpu_polled >= GPU_POLL_SECONDS:
            self.gpu_polled = now
            self.gpu = gpu_reading()
        if now - self.references_pruned >= REFERENCE_PRUNE_SECONDS and self.config is not None:
            self.references_pruned = now
            self.prune_references(now)

    def prune_references(self, now: float) -> None:
        """Delete reference pictures no zone or line uses once they are a day old.

        The editor keeps a picture every time it opens, and only the ones saved with zones stay in use.

        Args:
            now: Current time.
        """
        used = {
            item.reference
            for view in self.config.views
            for item in (*view.rules.zones, *view.rules.lines)
            if item.reference
        }
        root = self.settings.events_dir
        for path in (root / "analytics" / "references").glob("*/*.jpg"):
            relative = path.relative_to(root).as_posix()
            try:
                old = now - path.stat().st_mtime > REFERENCE_KEEP_SECONDS
            except FileNotFoundError:
                continue
            if old and relative not in used:
                path.unlink(missing_ok=True)

    def observe(self, now: float) -> None:
        """Send the per view counts and states to the bus every few seconds.

        Args:
            now: Current time.
        """
        if not self.sink.active or now - self.observed < OBSERVATION_SECONDS:
            return
        self.observed = now
        status = self.status()
        views = []
        for view in status["views"]:
            rules = view.pop("rules") or {}
            rules.pop("identities", None)
            rules.pop("open", None)
            views.append({**view, "counts": rules})
        body = {"at": now, "pipeline": status["pipeline"], "gpu": status["gpu"], "views": views}
        self.sink.observations(body, self.owner)

    def publish_face(self, body: dict) -> None:
        """Send a face result to the backend.

        Args:
            body: The face result.
        """
        if self.outbox_backend is not None:
            self.outbox_backend.send("/internal/people/faces", body)

    def related_event(self, view_id: str, track: int) -> str | None:
        """Find the analytics event a person track of a view belongs to.

        The rules engine may have joined the track into an identity started by an earlier tracker id, and the
        events list identities, so the track is looked up through the engine's track book.

        Args:
            view_id: The view.
            track: Tracker id of the person.

        Returns:
            The id of the newest event of the view that involves the person, or None.
        """
        with self.lock:
            runtime = self.views.get(view_id)
            if runtime is None or runtime.engine is None:
                return None
            identity = runtime.engine.book.by_track.get(track, track)
            for event in reversed(self.recent):
                if event.get("view") == view_id and identity in (event.get("objects") or []):
                    return event["id"]
        return None

    def view_snapshot(self, view_id: str, reference: bool) -> tuple[bytes, str | None] | None:
        """Cut a fresh picture of a view from its camera's newest segment.

        Args:
            view_id: The view.
            reference: Also keep the picture as a reference snapshot for zones drawn on it.

        Returns:
            The JPEG and, when kept, its path relative to the events folder; None when no picture is available.
        """
        with self.lock:
            runtime = self.views.get(view_id)
            if runtime is None:
                return None
            view = runtime.config
        data = latest_frame(self.settings.buffer_dir / view.camera, view.lens, view.lenses)
        if data is None:
            return None
        relative = None
        if reference:
            relative = f"analytics/references/{view_id}/{int(time.time())}.jpg"
            target = self.settings.events_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return data, relative

    def live_objects(self, view_id: str) -> dict | None:
        """Return the tracked objects of a view's latest frame.

        Args:
            view_id: The view.

        Returns:
            The objects with their frame time, or None for an unknown view.
        """
        with self.lock:
            runtime = self.views.get(view_id)
            if runtime is None:
                return None
            return {"view": view_id, "at": runtime.last_frame_at, "objects": list(runtime.objects)}

    def status(self) -> dict:
        """Return the full service status.

        Returns:
            Pipeline, views, recorders, GPU and recent events.
        """
        now = time.time()
        with self.lock:
            views = []
            for view_id, runtime in self.views.items():
                view = runtime.config
                stalled = view.detects and (
                    runtime.last_frame_at is None or now - runtime.last_frame_at > STALL_SECONDS
                )
                views.append(
                    {
                        "id": view_id,
                        "name": view.name,
                        "camera": view.camera,
                        "lens": view.lens,
                        "profile": view.profile.name,
                        "enabled": view.enabled,
                        "analysed": view.detects,
                        "target_fps": view.fps,
                        "fps": runtime.fps(now),
                        "latency": runtime.latency(),
                        "frames": runtime.frames,
                        "objects": len(runtime.objects),
                        "last_frame_at": runtime.last_frame_at,
                        "state": "disabled" if not view.enabled else ("stalled" if stalled else "live"),
                        "events": runtime.events,
                        "suspended": sorted(runtime.suspended),
                        "scene": self.scenes.report(view_id),
                        "rules": runtime.engine.snapshot() if runtime.engine else None,
                        "tamper": (
                            {"active": sorted(runtime.tamper.active), **runtime.tamper.last} if runtime.tamper else None
                        ),
                        "errors": list(runtime.errors),
                    }
                )
            recorders = {
                camera_id: {
                    "running": recorder.running,
                    "restarts": recorder.restarts,
                    "last_keyframe": recorder.last_keyframe,
                    "errors": list(recorder.errors)[-5:],
                }
                for camera_id, recorder in self.recorders.items()
            }
            return {
                "uptime_seconds": round(now - self.started_at),
                "config_version": self.config.version if self.config else None,
                "arm_mode": self.arm_mode().value,
                "detector": self.settings.detector,
                "pipeline": {
                    "state": self.pipeline_state,
                    "message": redact(self.pipeline_message),
                    "restarts": self.pipeline_restarts,
                    "dropped_batches": self.dropped_batches,
                },
                "gpu": self.gpu,
                "views": views,
                "recorders": recorders,
                "clip_encoder": self.clips.working,
                "pending_clips": len(self.pending),
                "backend": (
                    {
                        "delivered": self.outbox_backend.delivered,
                        "pending": self.outbox_backend.items.qsize(),
                        "failures": self.outbox_backend.failures,
                        "last_error": self.outbox_backend.last_error,
                    }
                    if self.outbox_backend
                    else None
                ),
                "bus": self.sink.status(),
                "recent_events": list(self.recent)[-50:],
                "faces": {
                    **self.faces.status(),
                    "note": self.faces_note,
                    "gallery_error": self.gallery_sync.error if self.gallery_sync else None,
                },
                "features": {
                    **self.features.status(),
                    "persons_note": self.persons_note,
                    "plates_note": self.plates_note,
                },
            }


def gpu_reading() -> dict:
    """Read the GPU's memory and utilisation with nvidia-smi.

    Returns:
        Used and total memory in MiB and utilisation in percent, empty when unavailable.
    """
    command = ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        used, total, utilisation = (int(value.strip()) for value in result.stdout.splitlines()[0].split(","))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {}
    return {"memory_used_mb": used, "memory_total_mb": total, "utilization_percent": utilisation}
