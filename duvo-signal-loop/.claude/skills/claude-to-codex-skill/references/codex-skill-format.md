# Codex CLI skill format — the facts that drive a correct port

Verified against the installed **codex-cli 0.135.0** (its bundled
`quick_validate.py` and the live model-visible prompt via
`codex debug prompt-input`). When in doubt, re-verify on the user's binary
rather than trusting blog posts — the format has moved fast.

## Table of contents
1. Where Codex discovers skills
2. SKILL.md frontmatter rules (STRICT — Codex validates these)
3. How Codex skills are invoked (and the `$ARGUMENTS` trap)
4. Tool-name translation (Claude → Codex)
5. `agents/openai.yaml` (optional UI metadata)
6. Porting checklist

## 1. Where Codex discovers skills

Codex scans these roots; each skill is a folder with a `SKILL.md`. Confirmed
discovered in 0.135.0 (closer-to-cwd wins on name clashes):

| Scope | Path |
|---|---|
| Project (repo-local) | `<repo>/.codex/skills/<name>/` **and** `<repo>/.agents/skills/<name>/` |
| User global | `~/.codex/skills/<name>/` **and** `~/.agents/skills/<name>/` |
| Codex system (bundled) | `~/.codex/skills/.system/` |

- `.agents/skills` is the cross-agent "open skills standard" (read by Claude too);
  `.codex/skills` is the Codex-specific analog of Claude's `.claude/skills`.
- **For a clean 1:1 mirror of a project's `.claude/skills`, use `.codex/skills`**
  (symmetric, and Claude Code won't double-load it).
- Verify what Codex actually sees with:
  `codex debug prompt-input "hi" | grep <skill-name>` — the prompt lists every
  discovered skill as `- name: description (file: /abs/path/SKILL.md)`.
- After adding/changing skills, **restart Codex** if they don't appear.

## 2. SKILL.md frontmatter rules (Codex VALIDATES these)

Codex rejects unknown keys and malformed values outright. Allowed keys:

```
name, description, license, allowed-tools, metadata
```

(`allowed-tools` is *accepted but ignored* by Codex — it does not restrict tools.
A Claude skill's `name`+`description` frontmatter already passes.)

Hard constraints:
- **`name`**: `^[a-z0-9-]+$`, ≤64 chars, no leading/trailing/consecutive hyphens.
- **`description`**: ≤1024 chars, and **must NOT contain `<` or `>`** (angle
  brackets are rejected). This is the most common silent breakage when porting a
  Claude description that uses `<thing>` placeholders or HTML-ish text. Replace
  `<x>` with `[x]` or rephrase. (Arrows like `→` are fine.)
- The `description` is Codex's **primary trigger signal** — keep the "when to use"
  (and ideally "when NOT to use") in it, not buried in the body.

Validate any produced skill with Codex's own checker:
`python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-dir>`

## 3. Invocation and the `$ARGUMENTS` trap

Codex skills are invoked by: typing `$skill-name`, `/skills`, naming the skill in
the prompt, or **implicit** model-triggering from the `description` (gated by
`policy.allow_implicit_invocation` in `agents/openai.yaml`, default true).

**Codex skills do NOT expand `$ARGUMENTS`, `$1`, `$2`.** That substitution is a
Claude Code (and Codex's deprecated `~/.codex/prompts/`) feature, not a skills
feature. Any Claude skill body that says "read the target from `$ARGUMENTS`" must
be rewritten to **infer the input from the user's natural-language request**, e.g.
"determine the target file/subdir from what the user asked." Optionally seed a
scaffold via `interface.default_prompt` in `agents/openai.yaml`.

## 4. Tool-name translation (Claude → Codex)

Codex is literal: if a skill says "use the `TodoWrite` tool", Codex may hunt for a
tool by that exact name and stall. Replace named-tool references with Codex
equivalents or plain verbs:

| Claude reference | Codex equivalent |
|---|---|
| `Bash` | `shell` (run the command) |
| `Edit` / `Write` | `apply_patch` (apply a patch to the file) |
| `Read` | `read_file` (or just "open/read the file") |
| `Grep` | `shell` + `rg`/`grep` |
| `Glob` | `shell` + `rg --files` / `find` |
| `TodoWrite` | `update_plan` |
| `Task` / subagents | **none** — flatten to sequential steps |
| `Skill` tool / `/skill` | **none** — reference other skills as prose |
| `WebFetch` / `WebSearch` | not built-in — needs an MCP/config; declare it or note the prerequisite |

Most well-written skills describe *what to do* ("read the file", "run the script")
rather than naming tools, and port unchanged. Only fix explicit tool-name callouts.

## 5. `agents/openai.yaml` (optional)

Optional per-skill UI/policy metadata Codex reads (not the model). A skill works
with just `SKILL.md`. Schema (quote strings, keep keys unquoted):

```yaml
interface:
  display_name: "Human-facing name"
  short_description: "25–64 char UI blurb"
  icon_small: "./assets/small.svg"
  icon_large: "./assets/large.png"
  brand_color: "#3B82F6"
  default_prompt: "Use $skill-name to ..."   # MUST mention the skill as $skill-name
policy:
  allow_implicit_invocation: true            # false = only explicit $skill / /skills
dependencies:
  tools:
    - type: "mcp"
      value: "serverName"
      description: "..."
      transport: "streamable_http"
      url: "https://..."
```

Generate deterministically with codex's bundled
`~/.codex/skills/.system/skill-creator/scripts/generate_openai_yaml.py <skill-dir> --interface key=value`.
Only add it when the user wants UI polish or must declare an MCP dependency.

## 6. Porting checklist

1. Scaffold with `scripts/convert_skill.py <name>` (copies resources, rewrites
   `.claude/skills` → `.codex/skills`, validates frontmatter).
2. Fix frontmatter if flagged (angle brackets in description, bad name).
3. In the new SKILL.md body: remove `$ARGUMENTS`/`$1` reliance → read from the
   request; translate named Claude tools; flatten subagent/`Skill`-tool steps;
   repoint any `.claude`/`CLAUDE.md` mentions to `.codex`/`AGENTS.md`.
4. Confirm bundled scripts still resolve their own paths (a script that walks N
   levels up to the repo root works only if the new skill sits at the same depth —
   `.codex/skills/<name>/scripts/` is the same depth as `.claude/skills/<name>/scripts/`, so it does).
5. Validate: `quick_validate.py <dest>` → "Skill is valid!".
6. Confirm discovery: `codex debug prompt-input "hi" | grep <name>`.
