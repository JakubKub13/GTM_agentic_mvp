---
name: claude-to-codex-skill
description: >-
  Convert (port / mirror) an existing Claude Code skill into an OpenAI Codex CLI
  skill so the same capability is available when running the codex agent. The
  skill name to convert is passed as $ARGUMENTS (e.g. "/claude-to-codex-skill
  understand-codebase"). Use this whenever the user asks to "convert a skill to
  codex", "mirror/port a skill to codex", "make my .claude skill work in codex",
  "give codex the same skills", "duplicate this skill for codex CLI", or names a
  skill plus codex — even if they don't say the word "skill". Handles the Codex
  format differences automatically: strict frontmatter validation (no angle
  brackets in description, hyphen-case name), the $ARGUMENTS-isn't-substituted
  trap, Claude→Codex tool-name translation, and .claude→.codex path rewriting.
---

# Convert a Claude Code skill into a Codex CLI skill

Codex CLI has a near-identical skill system to Claude Code — a folder with a
`SKILL.md` (YAML frontmatter + markdown) plus optional `scripts/`, `references/`,
`assets/`. The differences are small but sharp, and a few of them break
**silently**. This skill ports a source skill correctly and leaves you with a
Codex-valid, discoverable skill.

The skill to convert is given as **`$ARGUMENTS`** (a skill name, e.g.
`understand-codebase`). If `$ARGUMENTS` is empty, ask the user which skill to
convert, or list `.claude/skills/*/` and offer them.

## Why this isn't just a copy

Four things differ between the two formats; the first two fail silently:

1. **`$ARGUMENTS` / `$1` are NOT substituted inside a Codex skill.** That is a
   Claude feature. A ported body that says "read the target from `$ARGUMENTS`"
   will not work — rewrite it to read the input from the user's request.
2. **Codex strictly validates frontmatter.** The `description` must not contain
   `<` or `>` and must be ≤1024 chars; `name` must be hyphen-case. Unknown
   frontmatter keys outside `{name, description, license, allowed-tools, metadata}`
   are rejected.
3. **Tool names differ.** Explicit references to Claude tools (`Read`, `Edit`,
   `Bash`, `TodoWrite`, `Task`/subagents, the `Skill` tool) confuse Codex, which
   is literal. Translate them or use plain verbs.
4. **Paths.** `.claude/skills/...` and `CLAUDE.md` references must become
   `.codex/skills/...` / `AGENTS.md`.

Full details, the discovery roots, and the tool mapping table live in
`references/codex-skill-format.md` — read it if anything below is unclear or the
skill is unusual.

## Workflow

### 1. Scaffold with the helper script

Run the bundled script with the skill name. It locates the source under the
standard `.claude/skills` roots, creates the mirror under `.codex/skills/`
(project-local by default, so Codex discovers it in this repo), copies bundled
resources, rewrites `.claude/skills` → `.codex/skills` inside text files, and
validates the frontmatter against Codex's real rules:

```bash
.claude/skills/claude-to-codex-skill/scripts/convert_skill.py <skill-name>
```

Useful flags: `--force` (overwrite an existing dest), `--dest-root ~/.codex/skills`
(install user-global instead of project-local), `--source-root DIR` (non-standard
source location).

The script prints two things you must act on:
- a **frontmatter validation** result (fix any `FAIL` before continuing), and
- a **judgment-edits checklist** — the things it deliberately did not auto-change
  because they need a model's reading of intent.

### 2. Make the judgment edits in the new SKILL.md

Open the generated `.codex/skills/<skill-name>/SKILL.md` and apply the checklist:

- **Remove `$ARGUMENTS` / `$1` reliance.** Replace "read X from `$ARGUMENTS`" with
  "infer X from the user's request" (and note Codex doesn't substitute it). Keep a
  literal `$skill-name` mention in the *description* though — that is valid Codex
  invocation syntax, not a substitution.
- **Translate named Claude tools** to Codex equivalents (shell / apply_patch /
  read_file / update_plan) or plain verbs. See the table in the reference.
- **Flatten subagent / `Skill`-tool steps** — Codex has neither; make them
  sequential prose.
- **Fix the description if flagged** — strip angle brackets, shorten to ≤1024.
- Leave genuinely portable content (reading orders, domain knowledge, workflows)
  unchanged. Most of a good skill ports as-is; only touch what the checklist flags.

If the source is tool-agnostic (describes *what to do*, not which tool to use),
there may be nothing to edit beyond the script's automatic path rewrite — still
re-read the body once to be sure.

### 3. Validate and confirm discovery

```bash
# Codex's own validator — must print "Skill is valid!"
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py .codex/skills/<skill-name>

# Confirm Codex actually discovers it (lists every skill it sees)
codex debug prompt-input "hi" | grep <skill-name>
```

A path under the repo's `.codex/skills/` in that grep output means Codex will load
it when run in this repo. Tell the user to **restart Codex** if a running session
doesn't show it yet.

### 4. Report

Summarize: source → dest path, which resources were copied, which judgment edits
you made, and the validation result. If the user wanted UI metadata (display name,
icon, MCP dependency), offer to add `agents/openai.yaml` — see the reference for
its schema and the bundled `generate_openai_yaml.py`.

## Converting several skills

To mirror an entire `.claude/skills/` directory, run step 1's script once per
skill folder, then do the per-skill judgment pass. Skills with no Claude-specific
tool/`$ARGUMENTS` usage often need only the automatic scaffold.
