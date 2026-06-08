# The 6 Dynamic Workflow Patterns — distilled

> Faithful text distillation of `Dynamic_Workflow_Patterns.pdf` (Mark Kashef, *The 6 Dynamic
> Workflow Patterns*). The PDF is the source artifact and sits beside this file; this markdown
> is the working reference so the skill never depends on PDF-rendering tools. When in doubt,
> the PDF wins.

## The one idea behind all six

A dynamic workflow spins up a **team of separate Claudes** instead of doing everything in one
context window. A single window breaks down on a big job in three ways — and a workflow fixes
each by construction, not by asking nicely:

| Failure mode | What it looks like | Why a workflow fixes it |
|---|---|---|
| **Laziness** | Stops early, declares a multi-part job "done" after partial progress | Each piece is its own agent with its own narrow job; nothing gets silently skipped |
| **Self-preference** | Biased toward its own answers when asked to check its own work | The checker is a *different* agent than the maker — it has no ego in the output |
| **Goal drift** | Loses edge cases and "do not do X" rules as it compresses over many turns | A deterministic harness (loop/bracket) holds the rules; agents stay short-lived |

The six patterns are the whole alphabet. Almost every real workflow is one of these, or a few
**stacked together**. In build order, simplest first:

---

## Pattern 1 · Classify and act

**When:** a flood of mixed items that each need the right specialist — support queues, inboxes,
incoming requests, routing tasks to the right model.

**How:** a classifier agent reads each item and routes it to the right handler (bug / refund /
lead / spam …). **Quarantine** the agents that read untrusted text so they can only classify and
summarize, never take actions — a separate *trusted* agent does the actual routing and replies.
Dedup against what's already tracked before any handler acts. Pair with `/loop` to keep clearing
the queue as new items land.

**Key moves:** classifier → specialist handlers · quarantine read-from-untrusted agents ·
dedup before acting · `/loop` for continuous queues.

---

## Pattern 2 · Fan out and synthesize

**When:** one big job that splits into many independent pieces — deep research, due diligence,
reading a whole pile of files at once.

**How:** fan out **one subagent per piece, each in its own clean context** so the pieces never
cross-contaminate. Every agent returns a **structured** summary with the **exact source path**
for each finding. Then a **barrier** synthesize step waits for *all* of them and merges their
outputs into one cited result, where every claim links back to where it came from.

**Key moves:** one clean context per piece · structured returns with sources · barrier before
synthesis · every claim traceable to its source.

---

## Pattern 3 · Adversarial verification

**When:** you need something genuinely checked, not graded by the agent that made it — claims,
reports, audits, rule adherence.

**How:** one agent extracts each claim into its own item; for every claim, a **separate** skeptic
agent checks it against the real source and flags anything that doesn't hold up. **The verifier
must be a different agent than the one that produced the claim** so it isn't rubber-stamping its
own work. Return the list of failures with the exact reason each failed.

**Key moves:** extract → verify-with-a-different-agent · skeptic prompted to *refute*, not bless ·
default-to-fail on weak evidence · report failures with reasons.

**This is the devil's-advocate gate.** Strengthen it when stakes are high: give N skeptics distinct
lenses (correctness / security / does-it-reproduce), or add a neutral **arbiter** agent that reads
both the validation and the refutation and issues a binding keep/kill verdict.

---

## Pattern 4 · Generate and filter

**When:** taste-based work where you want the best of many options — names, titles, headline
angles, designs, cold-email copy.

**How:** one generator agent makes **far more than you need**, fast and loose (e.g. 40 options).
A **separate** judge agent scores every option against a rubric, dedupes near-identical phrasings,
and returns only the top few with a one-line reason each. **Generator and judge must be different
agents** so the judge isn't grading its own ideas.

**Key moves:** over-generate · judge against an explicit rubric · dedupe · keep only the best few ·
maker ≠ judge.

---

## Pattern 5 · Tournament

**When:** ranking a big pile by judgment where a 1-to-10 score would be unreliable — resumes,
tickets by severity, inbound leads by fit.

**How:** run a **tournament of pairwise comparisons** against a rubric instead of scoring each one
cold — pairwise judgment beats scoring out of ten. Each head-to-head match is its own comparison
agent; a **deterministic loop holds the bracket** so only the running order stays in context, not
the whole pile. Once the bracket settles, spin off **fresh** agents to double-check the top finishers
against the same rubric and flag anyone ranked higher than they should be. Build the rubric *first*
— interview the user with `AskUserQuestion` before any comparing starts.

**Key moves:** pairwise > cold scores · deterministic bracket holds order · fresh agent per matchup ·
re-audit the top finishers · rubric built up front.

---

## Pattern 6 · Loop until done

**When:** an unknown amount of work, where you don't know how many passes it'll take — flaky tests,
cleanups, mining sessions for recurring mistakes.

**How:** keep forming theories / spawning new attempts (each in its own isolated worktree when they
mutate state) with **no fixed pass count**, looping until a **real stop condition** is met — e.g. one
theory reproduces the failure on demand *and* its fix makes the test pass clean. Pair with `/goal`
to set a hard completion requirement.

**Key moves:** no fixed count · spawn until a true stop condition · isolate mutating attempts ·
`/goal` enforces "don't stop until X is actually true."

---

## Bonus · Stack them (the composition capstone)

Real work usually chains a few patterns into one machine. The canonical stack is **fan out +
adversarial verify + loop until done**:

> Audit every file under `./codebase`, fan out one agent per file, have a *separate* agent try to
> refute each finding against the code, and loop until a clean pass turns up no new issues. Return
> only the confirmed issues, each with the file and exact line. `/goal` don't stop until a full
> clean pass finds no new issues.

Result: confirmed findings with almost no false alarms — because every finding survived a refutation
and the loop only ends when a fresh pass is dry.

---

## The control dials

- **`/goal`** — sets a hard completion requirement; the workflow doesn't stop until what you asked
  for is *actually true*. Use it whenever "looks done" isn't good enough.
- **`/loop`** — runs a workflow on a schedule; good for repeatable things like triage every morning.
- **Token cap** — add **"use 10k tokens"** (any number) right in the prompt and it won't blow past
  that. Always include a cap; deep web-research/verify loops get expensive fast.

## Build-your-own formula (from the PDF)

You don't design the JavaScript by hand. Describe the shape in plain English using the pattern words
and Claude writes the harness. The PDF's template:

> Use a workflow to `<do X over ./your_folder>`. Fan out one agent per item, have a separate agent
> verify each result, and loop until a clean pass finds nothing new. use 10k tokens.

## Trigger words

"build a workflow", "use a workflow", or just "ultracode". Confirm the planned workflow when Claude
shows it, then let it run. Cap cost any time by adding "use Nk tokens".
