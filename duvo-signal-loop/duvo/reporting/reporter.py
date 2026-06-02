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


def generate_report(results: list[RunResult], path: str = "output/run-report.html") -> str:
    """Render results to an HTML report file and return the output path.

    Results are sorted by score descending so the highest-scoring accounts
    appear first in the report.

    Args:
        results: List of :class:`~models.RunResult` objects from the pipeline.
        path:    Destination file path.  Parent directories are created
                 automatically when the path contains a directory component.

    Returns:
        The resolved *path* string (same value passed in).
    """
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    ordered = sorted(results, key=lambda r: r.score.score, reverse=True)
    html = _env.get_template("report.html").render(results=ordered)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)

    log.info("Report written to %s (%d accounts rendered)", path, len(results))
    return path
