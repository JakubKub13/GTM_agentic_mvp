#!/usr/bin/env python3
"""Scaffold a Codex CLI skill from an existing Claude Code skill.

This does the *mechanical, deterministic* half of a Claude -> Codex skill port:
locate the source skill, create the mirror under `.codex/skills/`, copy bundled
resources, rewrite `.claude/` path references to `.codex/`, and run a strict
validation pass against Codex's real frontmatter rules (extracted from the
installed codex-cli quick_validate.py).

It deliberately does NOT make the judgment edits that need a model — rewriting
`$ARGUMENTS` usage, translating Claude tool-name references, flattening
subagent/`Skill`-tool steps. Instead it prints a CHECKLIST of those so the
agent finishes them by hand. See ../references/codex-skill-format.md.

Usage:
    convert_skill.py <skill-name> [--source-root DIR] [--dest-root DIR] [--force]

Defaults: searches for the source skill in (in order) ./.claude/skills,
<repo-root>/.claude/skills, ~/.claude/skills. The destination mirrors the
source's parent with `.claude` swapped to `.codex` (so a project skill lands in
the project's `.codex/skills/`, a user skill in `~/.codex/skills/`).

Exit code 0 on a clean scaffold (validation passed), 1 on any error or if the
produced SKILL.md fails Codex's frontmatter rules.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# --- Codex frontmatter rules (mirrored from codex-cli quick_validate.py) -------
# Keep these in sync with references/codex-skill-format.md.
ALLOWED_FRONTMATTER_KEYS = {"name", "description", "license", "allowed-tools", "metadata"}
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
NAME_RE = re.compile(r"^[a-z0-9-]+$")

RESOURCE_DIRS = ("scripts", "references", "assets")


def find_source(skill_name: str, source_root: str | None) -> Path:
    """Return the source skill directory, searching the standard Claude roots."""
    if source_root:
        cand = Path(source_root).expanduser() / skill_name
        if (cand / "SKILL.md").is_file():
            return cand
        sys.exit(f"error: no SKILL.md at {cand}")

    here = Path.cwd()
    repo_root = _git_root(here)
    candidates = [
        here / ".claude" / "skills" / skill_name,
        (repo_root / ".claude" / "skills" / skill_name) if repo_root else None,
        Path.home() / ".claude" / "skills" / skill_name,
    ]
    for cand in candidates:
        if cand and (cand / "SKILL.md").is_file():
            return cand
    searched = "\n  ".join(str(c) for c in candidates if c)
    sys.exit(f"error: could not find skill {skill_name!r}. Searched:\n  {searched}")


def _git_root(start: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def dest_for(source: Path, dest_root: str | None) -> Path:
    """Mirror the source's parent, swapping `.claude` -> `.codex`."""
    if dest_root:
        return Path(dest_root).expanduser() / source.name
    parent = source.parent  # .../.claude/skills
    swapped = Path(str(parent).replace("/.claude/", "/.codex/"))
    if swapped == parent:  # parent didn't contain /.claude/ — fall back
        swapped = parent.parent / ".codex" / "skills"
    return swapped / source.name


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body). Exit if no valid frontmatter."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        sys.exit("error: source SKILL.md has no YAML frontmatter")
    return m.group(1), m.group(2)


def parse_simple_frontmatter(fm: str) -> dict:
    """Minimal top-level key parser (handles block scalars `>-`/`|`).

    Avoids a hard PyYAML dependency. Only needs top-level keys for validation.
    """
    data: dict[str, str] = {}
    lines = fm.split("\n")
    i = 0
    key_re = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
    while i < len(lines):
        line = lines[i]
        km = key_re.match(line)
        if not km:
            i += 1
            continue
        key, rest = km.group(1), km.group(2).strip()
        if rest in (">-", ">", "|", "|-", ">+", "|+"):
            block: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or lines[i] == ""):
                block.append(lines[i].strip())
                i += 1
            data[key] = " ".join(b for b in block if b)
            continue
        data[key] = rest.strip().strip('"').strip("'")
        i += 1
    return data


def validate_frontmatter(fm_text: str) -> list[str]:
    """Return a list of human-readable problems (empty == valid for Codex)."""
    problems: list[str] = []
    data = parse_simple_frontmatter(fm_text)

    unexpected = set(data) - ALLOWED_FRONTMATTER_KEYS
    if unexpected:
        problems.append(
            f"frontmatter has keys Codex rejects: {', '.join(sorted(unexpected))} "
            f"(allowed: {', '.join(sorted(ALLOWED_FRONTMATTER_KEYS))})"
        )
    name = data.get("name", "")
    if not name:
        problems.append("missing `name`")
    elif not NAME_RE.match(name):
        problems.append(f"name {name!r} must be hyphen-case [a-z0-9-]")
    elif name.startswith("-") or name.endswith("-") or "--" in name:
        problems.append(f"name {name!r} cannot start/end with or contain double hyphens")
    elif len(name) > MAX_NAME_LENGTH:
        problems.append(f"name too long ({len(name)} > {MAX_NAME_LENGTH})")

    desc = data.get("description", "")
    if not desc:
        problems.append("missing `description`")
    else:
        if "<" in desc or ">" in desc:
            problems.append("description contains angle brackets (< or >) — Codex REJECTS these")
        if len(desc) > MAX_DESCRIPTION_LENGTH:
            problems.append(f"description too long ({len(desc)} > {MAX_DESCRIPTION_LENGTH})")
    return problems


def rewrite_claude_paths(text: str) -> str:
    """Swap `.claude/skills` path references to `.codex/skills` in skill text."""
    return text.replace(".claude/skills", ".codex/skills")


def copy_resources(source: Path, dest: Path) -> list[str]:
    copied = []
    for rd in RESOURCE_DIRS:
        src_dir = source / rd
        if not src_dir.is_dir():
            continue
        dst_dir = dest / rd
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        # Rewrite .claude path refs inside text resources (scripts/refs), keep perms.
        for f in dst_dir.rglob("*"):
            if f.is_file() and f.suffix in {".sh", ".py", ".md", ".txt", ".js", ".ts", ".rb"}:
                try:
                    original = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                rewritten = rewrite_claude_paths(original)
                if rewritten != original:
                    f.write_text(rewritten, encoding="utf-8")
        copied.append(rd)
    return copied


def run_codex_validator(dest: Path) -> str | None:
    """Run codex-cli's own quick_validate.py if reachable. Return its message or None."""
    candidates = [
        Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py",
    ]
    for v in candidates:
        if v.is_file():
            try:
                out = subprocess.run(
                    [sys.executable, str(v), str(dest)],
                    capture_output=True,
                    text=True,
                )
                return f"[codex quick_validate] {out.stdout.strip() or out.stderr.strip()}"
            except Exception as exc:  # noqa: BLE001
                return f"[codex quick_validate] could not run: {exc}"
    return None


JUDGMENT_CHECKS = [
    (
        r"\$ARGUMENTS|\$\{ARGUMENTS\}|\$\d",
        "Uses $ARGUMENTS/$1 — Codex skills do NOT substitute these. "
        "Rewrite to read the input from the user's natural-language request.",
    ),
    (
        r"\bSkill tool\b|\bvia the Skill tool\b",
        "References the `Skill` tool — Codex has no callable Skill tool. "
        "Reword cross-skill references as prose (e.g. 'also apply the X workflow').",
    ),
    (
        r"\bsubagent|\bTask tool\b|dispatch.*agent|spawn .*agent",
        "References subagents/Task — Codex has no subagents. Flatten to sequential instructions.",
    ),
    (
        r"\bRead tool\b|\bEdit tool\b|\bWrite tool\b|\bBash tool\b|\bGrep tool\b|\bGlob tool\b|\bTodoWrite\b|\bWebFetch\b|\bWebSearch\b",
        "Names Claude tools — Codex uses shell/apply_patch/read_file/update_plan. Use plain verbs or Codex names.",
    ),
    (
        r"\.claude\b|CLAUDE\.md|~/\.claude",
        "Mentions .claude / CLAUDE.md — repoint to .codex / AGENTS.md / ~/.codex.",
    ),
    (
        r"/skill[- ]name|slash command|/<skill",
        "Claude slash-command framing — Codex invokes via `$skill-name`, `/skills`, or implicit match.",
    ),
]


def scan_body_for_judgment(body: str) -> list[str]:
    hits = []
    for pattern, advice in JUDGMENT_CHECKS:
        if re.search(pattern, body, re.IGNORECASE):
            hits.append(advice)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a Codex skill from a Claude skill.")
    ap.add_argument("skill_name")
    ap.add_argument(
        "--source-root", help="dir containing <skill-name>/ (default: search .claude/skills roots)"
    )
    ap.add_argument(
        "--dest-root", help="dir to create <skill-name>/ under (default: mirror, .claude->.codex)"
    )
    ap.add_argument("--force", action="store_true", help="overwrite an existing destination skill")
    args = ap.parse_args()

    source = find_source(args.skill_name, args.source_root)
    dest = dest_for(source, args.dest_root)

    print(f"source : {source}")
    print(f"dest   : {dest}")

    if dest.exists() and not args.force:
        sys.exit(f"error: {dest} already exists (use --force to overwrite)")

    src_text = (source / "SKILL.md").read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(src_text)

    # Scaffold
    dest.mkdir(parents=True, exist_ok=True)
    new_skill_md = rewrite_claude_paths(src_text)
    (dest / "SKILL.md").write_text(new_skill_md, encoding="utf-8")
    copied = copy_resources(source, dest)

    print(f"copied resources: {', '.join(copied) if copied else '(none)'}")

    # Validate frontmatter against Codex rules
    problems = validate_frontmatter(fm_text)
    print("\n=== Codex frontmatter validation ===")
    if problems:
        for p in problems:
            print(f"  FAIL: {p}")
    else:
        print("  OK: name + description satisfy Codex rules")

    codex_msg = run_codex_validator(dest)
    if codex_msg:
        print(f"  {codex_msg}")

    # Body judgment checklist (the part this script can't safely automate)
    body_rewritten = rewrite_claude_paths(body)
    hits = scan_body_for_judgment(body_rewritten)
    print("\n=== JUDGMENT EDITS the agent must make in the new SKILL.md ===")
    if hits:
        for h in hits:
            print(f"  - {h}")
    else:
        print("  (none detected — body looks portable; still re-read it once)")

    print(f"\nScaffold written to: {dest}/SKILL.md")
    print("Next: open the new SKILL.md, apply the judgment edits above, then re-run")
    print("the codex validator on it.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
