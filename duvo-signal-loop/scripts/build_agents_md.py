"""Generate the Codex ``AGENTS.md`` from the Claude ``.claude/rules/`` files.

The ``.claude/rules/*.md`` files are the single source of truth for this repo's
house style. Claude Code reads them directly (glob-scoped via ``paths:``
frontmatter); the OpenAI Codex CLI cannot — it reads a single ``AGENTS.md`` at
the project root. This script concatenates the rule files into that ``AGENTS.md``
so both tools stay in lockstep with one source.

Run from the project root::

    uv run python scripts/build_agents_md.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Always-relevant rules first; the rest are appended in sorted order so a new
# rule file is picked up automatically without editing this list.
_ORDER = ["architecture.md", "python-style.md", "agents.md", "writeback.md", "tests.md"]

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
# ``-[ \t]+`` (dash then at least one space) matches list items but not the
# closing ``---`` frontmatter delimiter, so the latter is never read as a glob.
_PATHS = re.compile(r"^paths:[ \t]*\n((?:[ \t]*-[ \t]+.+\n?)+)", re.MULTILINE)
_MAX_BYTES = 32 * 1024  # Codex project_doc_max_bytes default

_BANNER = (
    "<!-- GENERATED from .claude/rules/ by scripts/build_agents_md.py — "
    "do not edit by hand; re-run after changing a rule. -->\n\n"
    "# AGENTS.md — duvo-signal-loop house rules\n\n"
    "Coding conventions for this repo, generated from `.claude/rules/` so Codex "
    "and Claude Code share one source of truth. Edit the rule files, not this one."
)


def _scope_note(frontmatter: str) -> str:
    """Render a human scope note from a rule's ``paths:`` frontmatter, or ``""``.

    Codex has no glob gating, so the scope that Claude enforces via ``paths:`` is
    surfaced as plain prose instead.

    Args:
        frontmatter: The raw leading ``---``-delimited block (may be empty).

    Returns:
        A one-line ``> Applies when working in ...`` note, or an empty string
        when the rule has no ``paths:`` (i.e. it always applies).
    """
    match = _PATHS.search(frontmatter)
    if not match:
        return ""
    globs = [
        line.strip().lstrip("-").strip().strip('"')
        for line in match.group(1).splitlines()
        if line.strip()
    ]
    joined = ", ".join(f"`{g}`" for g in globs)
    return f"> Applies when working in {joined}.\n\n"


def _section(path: Path) -> str:
    """Return one rule file as an AGENTS.md section: frontmatter stripped, scope inlined."""
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    frontmatter = match.group(0) if match else ""
    body = _FRONTMATTER.sub("", text, count=1).strip()
    note = _scope_note(frontmatter)
    # Insert the scope note right after the section's leading heading line.
    if note and body.startswith("#"):
        head, _, rest = body.partition("\n")
        return f"{head}\n\n{note}{rest.lstrip()}"
    return f"{note}{body}"


def build(rules_dir: Path) -> str:
    """Concatenate the rule files under *rules_dir* into the AGENTS.md body.

    Args:
        rules_dir: Path to ``.claude/rules/``.

    Returns:
        The full AGENTS.md text (banner + sections joined by blank lines).
    """
    files = {p.name: p for p in rules_dir.glob("*.md")}
    ordered = [files[name] for name in _ORDER if name in files]
    ordered += [files[name] for name in sorted(files) if name not in _ORDER]
    sections = [_BANNER] + [_section(p) for p in ordered]
    return "\n\n".join(sections) + "\n"


def main() -> int:
    """Build ``AGENTS.md`` at the project root and report its size."""
    project_root = Path(__file__).resolve().parent.parent
    rules_dir = project_root / ".claude" / "rules"
    if not rules_dir.is_dir():
        print(f"error: no rules directory at {rules_dir}", file=sys.stderr)
        return 1

    content = build(rules_dir)
    out = project_root / "AGENTS.md"
    out.write_text(content, encoding="utf-8")

    size = len(content.encode("utf-8"))
    print(f"wrote {out} ({size} bytes)")
    if size > _MAX_BYTES * 0.9:
        print(f"warning: approaching Codex 32 KiB cap ({size} / {_MAX_BYTES})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
