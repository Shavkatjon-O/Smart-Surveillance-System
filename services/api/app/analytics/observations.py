"""Latest observations the analytics service reported: pipeline state, GPU and per view status."""

from datetime import UTC, datetime


def view_status(view: dict, now: datetime) -> dict:
    """Pick the fields of a view's status that the API shows.

    Args:
        view: The view as the analytics service reports it.
        now: Time it arrived.

    Returns:
        The status.
    """
    counts = view.get("counts") or {}
    return {
        "state": view.get("state"),
        "fps": view.get("fps", 0.0),
        "target_fps": view.get("target_fps", 0.0),
        "latency": view.get("latency") or {},
        "objects": view.get("objects", 0),
        "events": view.get("events", 0),
        "occupancy": counts.get("occupancy") or {},
        "entries": counts.get("entries") or {},
        "crossings": counts.get("crossings") or {},
        "tamper": view.get("tamper"),
        "suspended": view.get("suspended") or [],
        "updated_at": now.isoformat(),
    }


class Observations:
    """Holds the latest report of the analytics service in memory.

    Attributes:
        pipeline: Pipeline state, None before the first report.
        gpu: GPU reading.
        views: Status per view id.
        updated_at: Time of the latest report.
    """

    def __init__(self):
        """Start empty."""
        self.pipeline: dict | None = None
        self.gpu: dict = {}
        self.views: dict[str, dict] = {}
        self.updated_at: datetime | None = None

    def store(self, report: dict) -> None:
        """Replace the state with a new report.

        Args:
            report: The report with pipeline, gpu and views.
        """
        now = datetime.now(UTC)
        self.pipeline = report.get("pipeline")
        self.gpu = report.get("gpu") or {}
        self.views = {str(view.get("id")): view_status(view, now) for view in report.get("views") or []}
        self.updated_at = now

    def store_view(self, report: dict) -> None:
        """Keep the latest observation of one view, as the bus carries them.

        Args:
            report: The observation with at, pipeline, gpu and view.
        """
        now = datetime.now(UTC)
        view = report.get("view") or {}
        if report.get("pipeline") is not None:
            self.pipeline = report.get("pipeline")
        self.gpu = report.get("gpu") or self.gpu
        self.views[str(view.get("id"))] = view_status(view, now)
        self.updated_at = now

    def view(self, view_uuid: str) -> dict | None:
        """Return the latest status of a view.

        Args:
            view_uuid: The view.

        Returns:
            The status, or None when the service has not reported the view.
        """
        return self.views.get(view_uuid)


observations = Observations()
