"""Delete Langfuse traces by name via the public REST API.

One-off cleanup utility. The ``probe`` traces this targets by default were
created by a manual ``as_type`` verification command during tracing setup — the
pipeline itself never emits them, so they will not reappear. Handy for clearing
any stray traces later, too.

Auth is HTTP Basic (public key = user, secret key = password); host/keys are read
from :mod:`duvo.config` (i.e. your ``.env``). Requires ``LANGFUSE_*`` to be set.

Usage:
    uv run python scripts/cleanup_langfuse_traces.py --dry-run          # list only
    uv run python scripts/cleanup_langfuse_traces.py                    # delete name=probe
    uv run python scripts/cleanup_langfuse_traces.py --name foo         # delete name=foo
"""

import argparse
import sys
from pathlib import Path

import httpx

# Allow running as a file from anywhere (sys.path[0] is scripts/, not the root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from duvo import config  # noqa: E402

_PAGE_LIMIT = 100
_DELETE_BATCH = 100


def _list_trace_ids(client: httpx.Client, name: str | None) -> list[str]:
    """Return trace ids (paginated). Filters by ``name`` unless it is ``None`` (all)."""
    ids: list[str] = []
    page = 1
    while True:
        params: dict[str, str | int] = {"page": page, "limit": _PAGE_LIMIT}
        if name is not None:
            params["name"] = name
        resp = client.get("/api/public/traces", params=params)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            break
        ids.extend(t["id"] for t in data if "id" in t)
        if len(data) < _PAGE_LIMIT:
            break
        page += 1
    return ids


def _delete_trace_ids(client: httpx.Client, ids: list[str]) -> None:
    """Delete traces in batches via DELETE /api/public/traces."""
    for start in range(0, len(ids), _DELETE_BATCH):
        batch = ids[start : start + _DELETE_BATCH]
        resp = client.request("DELETE", "/api/public/traces", json={"traceIds": batch})
        resp.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete Langfuse traces by name (or all).")
    parser.add_argument("--name", default="probe", help="Trace name to match (default: probe).")
    parser.add_argument(
        "--all", action="store_true", help="Delete ALL traces in the project (ignores --name)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List matching traces without deleting."
    )
    args = parser.parse_args()

    if not (config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set — nothing to do.", file=sys.stderr)
        return 1

    name = None if args.all else args.name
    scope = "ALL" if args.all else repr(name)

    with httpx.Client(
        base_url=config.LANGFUSE_HOST.rstrip("/"),
        auth=(config.LANGFUSE_PUBLIC_KEY, config.LANGFUSE_SECRET_KEY),
        timeout=30.0,
    ) as client:
        ids = _list_trace_ids(client, name)
        print(f"Found {len(ids)} trace(s) [{scope}] on {config.LANGFUSE_HOST}.")
        if not ids:
            return 0
        if args.dry_run:
            for tid in ids:
                print(f"  would delete {tid}")
            print("Dry run — nothing deleted. Re-run without --dry-run to delete.")
            return 0
        _delete_trace_ids(client, ids)
        print(f"Deleted {len(ids)} trace(s) [{scope}] (Langfuse processes deletions async).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
