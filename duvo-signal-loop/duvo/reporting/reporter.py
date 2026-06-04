"""Render an HTML audit log of the run — including each account's agent tool calls."""

import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from duvo.infra.logging_setup import get_logger
from duvo.models import RunResult

log = get_logger(__name__)

_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
    autoescape=select_autoescape(["html"]),
)


def generate_report(
    results: list[RunResult],
    path: str | None = None,
    run_id: str | None = None,
    run_date: str | None = None,
) -> str:
    """Render results to an HTML report file and return the output path.

    Results are sorted by score descending so the highest-scoring accounts
    appear first in the report.

    Args:
        results:  List of :class:`~models.RunResult` objects from the pipeline.
        path:     Destination file path.  When ``None``, a run-scoped path is
                  derived: ``output/run_reports/{run_date}_{run_id}-run-report.html``
                  if ``run_id`` is given, else ``output/run-report.html``.  Parent
                  directories are created automatically.
        run_id:   Batch run id.  Used to build the default path and rendered in
                  the report header so a report links back to its Langfuse trace
                  (it matches the trace's ``batch_run_id`` / session).
        run_date: ISO run date, used as a sortable filename prefix.

    Returns:
        The resolved *path* string.
    """
    if path is None:
        if run_id:
            stamp = f"{run_date}_{run_id}" if run_date else run_id
            path = f"output/run_reports/{stamp}-run-report.html"
        else:
            path = "output/run-report.html"

    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    ordered = sorted(results, key=lambda r: r.score.score, reverse=True)
    html = _env.get_template("report.html").render(results=ordered, run_id=run_id)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)

    log.info("Report written to %s (%d accounts rendered)", path, len(results))
    return path
