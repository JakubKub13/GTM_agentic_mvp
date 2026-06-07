export const meta = {
  name: 'web-frontend-build',
  description: 'Implement docs/web-frontend-plan.md end-to-end (FastAPI + Vite/React SPA) via a dependency-ordered, TDD, verify-every-unit pipeline. No git ops; dirty working tree only.',
  phases: [
    { title: 'Phase 0 — Map' },
    { title: 'Phase 1 — Pipeline refactor (#1–#6a)' },
    { title: 'Phase 2 — Backend FastAPI (#7–#17, #25)' },
    { title: 'Phase 3 — Frontend SPA (#18–#22, #24, #26)' },
    { title: 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof' },
  ],
}

// ───────────────────────── constants / args ─────────────────────────
const PLAN = (args && args.planPath) || '/Users/phantomghost/Desktop/GTM-duvo/duvo-signal-loop/docs/web-frontend-plan.md'
const WORKDIR = (args && args.workdir) || '/Users/phantomghost/Desktop/GTM-duvo/duvo-signal-loop'
const BASELINE = (args && args.baselineHead) || 'cf66c64afea2d8f36306c62bdfa23a45e58d9099'
const BASELINE_SHORT = (args && args.baselineShort) || 'cf66c64'
const PY = 'uv run pytest -q'
const VITEST = 'cd frontend && pnpm test'

const PREAMBLE = `SINGLE SOURCE OF TRUTH — read ${PLAN} IN FULL before doing anything. It is the LOCKED plan and the guard against goal drift; honor its "IMPLEMENTATION CONSTRAINTS" literally. Also read ./.claude/rules/architecture.md and ./.claude/rules/python-style.md.

ABSOLUTE RULES (violating any is a failure):
- NO git operations of ANY kind. Never run git add/commit/push/stash/checkout -b/branch/worktree/reset/restore/merge/rebase. The deliverable is an UNCOMMITTED dirty working tree on branch master for the human to review. Reading git state (git status/diff/log --oneline) is allowed; mutating it is FORBIDDEN.
- Work only in the local master working tree at ${WORKDIR}.
- TDD: write the FAILING test FIRST, then the implementation, then run the test and confirm it passes. Tests are OFFLINE — no network: mock Exa, the LLM provider, httpx, Google token verification, SSE peers. (Dependency INSTALL steps — uv add / pnpm install — may use the network; TESTS may not.)
- Stay strictly IN SCOPE. Edit ONLY the files assigned to your unit. Out of scope and an automatic FAIL: next.js / next / any Node backend or BFF; redis / celery / arq / any broker; postgres / psycopg / asyncpg; editing the agents' bounded-agency guards, scoring, or write-back semantics beyond what plan items #1–#17 require; importing an LLM provider outside duvo/llm/; importing langfuse outside duvo/infra/tracing.py; touching any file the plan did not authorize.
- House rules: async-first; one LLM boundary (duvo/llm/); one tracing boundary (duvo/infra/tracing.py); concern-per-folder; uv for python deps; pnpm for frontend; ruff clean (uv run ruff check) for python you touch.`

// ───────────────────────── schemas ─────────────────────────
const MAP_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['area', 'findings', 'tests', 'notes'],
  properties: {
    area: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['name', 'location', 'role'],
      properties: { name: { type: 'string' }, location: { type: 'string' }, role: { type: 'string' } } } },
    tests: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['summary', 'files_changed', 'plan_items', 'test_cmd', 'tests_green', 'test_output_tail'],
  properties: {
    summary: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    plan_items: { type: 'array', items: { type: 'string' } },
    test_cmd: { type: 'string' },
    tests_green: { type: 'boolean' },
    test_output_tail: { type: 'string' },
    notes: { type: 'string' },
  },
}

const SKEPTIC_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['satisfied', 'tests_green', 'invariant_checks', 'missing_or_violated', 'test_output_tail'],
  properties: {
    satisfied: { type: 'boolean' },
    tests_green: { type: 'boolean' },
    invariant_checks: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['invariant', 'met', 'evidence'],
      properties: { invariant: { type: 'string' }, met: { type: 'boolean' }, evidence: { type: 'string' } } } },
    missing_or_violated: { type: 'array', items: { type: 'string' } },
    test_output_tail: { type: 'string' },
  },
}

const SCOPE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['pass', 'branch', 'commits_made', 'new_branches', 'new_worktrees', 'new_stashes', 'tree_dirty_only', 'out_of_scope_findings', 'unauthorized_files'],
  properties: {
    pass: { type: 'boolean' },
    branch: { type: 'string' },
    commits_made: { type: 'boolean' },
    new_branches: { type: 'boolean' },
    new_worktrees: { type: 'boolean' },
    new_stashes: { type: 'boolean' },
    tree_dirty_only: { type: 'boolean' },
    out_of_scope_findings: { type: 'array', items: { type: 'string' } },
    unauthorized_files: { type: 'array', items: { type: 'string' } },
  },
}

const SUITE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['suite', 'green', 'passed', 'failed', 'output_tail'],
  properties: {
    suite: { type: 'string' }, green: { type: 'boolean' },
    passed: { type: 'integer' }, failed: { type: 'integer' }, output_tail: { type: 'string' },
  },
}

const CRITIC_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['items', 'silently_skipped'],
  properties: {
    items: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['item', 'has_test', 'has_diff', 'status', 'evidence'],
      properties: { item: { type: 'string' }, has_test: { type: 'boolean' }, has_diff: { type: 'boolean' }, status: { type: 'string' }, evidence: { type: 'string' } } } },
    silently_skipped: { type: 'array', items: { type: 'string' } },
  },
}

const PROOF_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['clean', 'branch', 'commits_made', 'new_branches', 'new_worktrees', 'git_status_tail', 'git_log_tail'],
  properties: {
    clean: { type: 'boolean' }, branch: { type: 'string' }, commits_made: { type: 'boolean' },
    new_branches: { type: 'boolean' }, new_worktrees: { type: 'boolean' },
    git_status_tail: { type: 'string' }, git_log_tail: { type: 'string' },
  },
}

// ───────────────────────── prompt builders ─────────────────────────
function implPrompt(u, feedback) {
  return `${PREAMBLE}

=== YOUR UNIT: ${u.id} — ${u.title} ===
PLAN ITEM(S) you must satisfy (re-read them verbatim in ${PLAN}):
${u.quote}

FILES YOU OWN — edit/create ONLY these (creating a new file is allowed only where the plan authorizes it):
${u.files.map((f) => '  - ' + f).join('\n')}

ORIENTATION MAP (from Phase 0; verify against the real code, do not trust blindly):
${u.map || '(none — read the files yourself)'}

TASK:
${u.task}

Run your unit's tests with: \`${u.testCmd}\`
${feedback ? '\n!!! A PRIOR ATTEMPT WAS REFUTED. You MUST address EVERY point below; do not repeat the mistake:\n' + feedback + '\n' : ''}
Deliver real, working code with tests you actually ran. tests_green must reflect a real run whose tail you paste. Do NOT touch files outside your ownership list. Do NOT run any git mutation.`
}

function skepticPrompt(u, impl) {
  return `${PREAMBLE}

You are a CORRECTNESS SKEPTIC. Your job is to REFUTE, never to approve. DEFAULT to satisfied=false. Mark satisfied=true ONLY if EVERY named invariant is PROVABLY met with file:line evidence. Do NOT vibe-check. You did not write this code; be adversarial.

=== UNIT UNDER REVIEW: ${u.id} — ${u.title} ===
PLAN ITEM(S) (quoted): ${u.quote}

REFUTATION RUBRIC — check each invariant CONCRETELY against the code:
${u.rubric}

The implementer claimed: ${JSON.stringify({ summary: impl.summary, files: impl.files_changed, tests_green: impl.tests_green })}

PROCEDURE:
1. Inspect the unit's diff: \`cd ${WORKDIR} && git --no-pager diff -- ${u.files.join(' ')}\` and read the changed files in full.
2. Re-run the unit's tests YOURSELF: \`${u.testCmd}\` — paste the tail. If not green, satisfied=false.
3. For EACH invariant in the rubric, record met=true/false with concrete evidence (file:line or test name). Any unmet invariant ⇒ satisfied=false and list it in missing_or_violated.
Do NOT edit any file. Do NOT run any git mutation.`
}

function scopePrompt(u) {
  return `${PREAMBLE}

You are the SCOPE GATE. Your ONLY job is the NEGATIVES — catch off-plan ADDITIONS and ANY git activity. You do NOT judge correctness. You did not write this code.

UNIT: ${u.id} — ${u.title}
AUTHORIZED FILES for this unit (anything else touched ⇒ unauthorized): ${u.files.join(', ')}

PROCEDURE — run each and report (read-only; NEVER mutate git):
  cd ${WORKDIR}
  git status --porcelain
  git stash list
  git branch --show-current
  git worktree list
  git --no-pager log --oneline -8

ASSERT (any failure ⇒ pass=false):
  - branch is exactly "master"
  - NO new commits: the newest commit MUST still be ${BASELINE_SHORT} (${BASELINE}). commits_made=true if HEAD moved.
  - NO new branches, NO new worktrees, NO new stashes beyond baseline (baseline had zero worktrees-besides-main and zero stashes).
  - tree is ONLY dirty (uncommitted modifications / untracked files) — tree_dirty_only=true.

Then grep the unit's diff and changed files for OUT-OF-SCOPE work — \`cd ${WORKDIR} && git --no-pager diff -- ${u.files.join(' ')}\` plus the changed files. FAIL (record in out_of_scope_findings) if you find ANY of:
  - next.js / next / a Node backend or BFF
  - redis / celery / arq / any broker
  - postgres / psycopg / asyncpg
  - edits to the agents' bounded-agency guards, scoring, or write-back semantics beyond what plan #1–#17 require
  - an LLM provider imported outside duvo/llm/
  - langfuse imported outside duvo/infra/tracing.py
List in unauthorized_files any changed file NOT in this unit's authorized list. Off-plan most often means doing EXTRA — that is exactly what you exist to catch. Do NOT edit anything. Do NOT run any git mutation.`
}

// ───────────────────────── core: build + verify one unit ─────────────────────────
async function buildUnit(u, phaseTitle) {
  let feedback = ''
  for (let attempt = 1; attempt <= 3; attempt++) {
    const impl = await agent(implPrompt(u, feedback), {
      label: `impl:${u.id}${attempt > 1 ? '#' + attempt : ''}`,
      phase: phaseTitle, schema: IMPL_SCHEMA,
    })
    if (!impl) { feedback = 'Implementer died; retry from scratch.'; continue }

    // Two-part verification — author is NEVER the signer. Both required (barrier).
    const [skeptic, scope] = await parallel([
      () => agent(skepticPrompt(u, impl), { label: `skeptic:${u.id}`, phase: phaseTitle, schema: SKEPTIC_SCHEMA }),
      () => agent(scopePrompt(u), { label: `scope:${u.id}`, phase: phaseTitle, schema: SCOPE_SCHEMA, model: 'sonnet' }),
    ])

    const correctnessOK = skeptic && skeptic.satisfied && skeptic.tests_green
    const scopeOK = scope && scope.pass && !scope.commits_made
    if (correctnessOK && scopeOK) {
      log(`✓ ${u.id} done (attempt ${attempt})`)
      return { unit: u.id, title: u.title, plan_items: u.items, status: 'done', attempts: attempt, impl, skeptic, scope }
    }

    const parts = []
    if (!correctnessOK) parts.push('CORRECTNESS SKEPTIC REFUTED:\n' + JSON.stringify({
      satisfied: skeptic && skeptic.satisfied, tests_green: skeptic && skeptic.tests_green,
      missing_or_violated: (skeptic && skeptic.missing_or_violated) || ['skeptic unavailable'],
    }, null, 2))
    if (!scopeOK) parts.push('SCOPE GATE FAILED:\n' + JSON.stringify({
      commits_made: scope && scope.commits_made, unauthorized_files: (scope && scope.unauthorized_files) || [],
      out_of_scope_findings: (scope && scope.out_of_scope_findings) || ['scope gate unavailable'],
    }, null, 2))
    feedback = parts.join('\n\n')
    log(`✗ ${u.id} refuted on attempt ${attempt}; re-implementing with a fresh agent`)
  }
  log(`⚠ ${u.id} BLOCKED after 3 attempts`)
  return { unit: u.id, title: u.title, plan_items: u.items, status: 'blocked', reason: feedback }
}

async function runSuite(label, cmd, phaseTitle) {
  return agent(
    `${PREAMBLE}\n\nRun the FULL offline test suite and report results — do NOT edit any code, do NOT run git mutations.\nCommand: \`cd ${WORKDIR} && ${cmd}\`\nPaste the final ~30 lines as output_tail and report passed/failed counts and whether it is fully green.`,
    { label, phase: phaseTitle, schema: SUITE_SCHEMA, model: 'sonnet' },
  )
}

// ═══════════════════════════ PHASE 0 — MAP ═══════════════════════════
phase('Phase 0 — Map')
const mapAreas = [
  { area: 'orchestrator', files: 'duvo/orchestrator.py', focus: 'run() signature/run_id (line ~193/232/235), load_companies, _process_account (persist/timeout/CancelledError sites), _persist_success, main() CLI arg wiring, http_client/exa_tool close in finally, tracing init/flush sites — for plan #1,#3,#6,#6a,#9,#10,#17.' },
  { area: 'agent_core', files: 'duvo/agent_core.py', focus: 'run_agent tool-call loop, the tool-call site (~line 91) and _short() (~line 140) — for the event-bus tool event #9.' },
  { area: 'store', files: 'duvo/store/db.py duvo/store/schema.sql duvo/store/runs.py duvo/store/account_runs.py duvo/store/events.py', focus: '_INITED gate + _connect schema init + _safe; start_run ON CONFLICT; upsert_account_run terminal fields; last_done_for_domain/done_domains_for_date queries; derive_action + record_event idempotency_key + UNIQUE(run_id,domain,channel) — for #3,#4,#5,#6a,#12,#15.' },
  { area: 'tests', files: 'tests/test_main.py tests/test_store_db.py tests/test_store_runs.py tests/test_store_account_runs.py tests/test_store_events.py tests/test_agent_core.py tests/conftest.py', focus: 'Existing offline patterns + the CLI guarantees (esp. test_main.py: dry-run persists nothing on the CLI path). Map fixtures/mocks reused for the new tests.' },
]
const maps = {}
const mapResults = await parallel(mapAreas.map((m) => () =>
  agent(
    `${PREAMBLE}\n\nREAD-ONLY mapping task — make NO edits, run NO git mutations. Read these files: ${m.files}\nProduce a precise map of the exact functions / line ranges that plan items #1–#17 will touch. Focus: ${m.focus}\nList the existing tests that cover this area (so later TDD extends them, not duplicates).`,
    { label: `map:${m.area}`, phase: 'Phase 0 — Map', schema: MAP_SCHEMA, model: 'sonnet', agentType: 'Explore' },
  ).then((r) => { if (r) maps[m.area] = r; return r })))
log(`Phase 0 complete: mapped ${mapResults.filter(Boolean).length}/${mapAreas.length} areas`)
const mapStr = (k) => maps[k] ? JSON.stringify(maps[k], null, 1).slice(0, 2500) : '(map unavailable)'

// ═══════════════════════════ PHASE 1 — PIPELINE REFACTOR (#1–#6a, +#15 schema foundation) ═══════════════════════════
// Sequential — orchestrator.py / db.py / schema.sql / runs.py / account_runs.py / events.py are shared; ONE owner at a time.
phase('Phase 1 — Pipeline refactor (#1–#6a)')
const P1 = [
  {
    id: 'P1a-schema-migrate', title: 'Versioned schema migration + new columns (#15, prerequisite for #1/#3/#6a)',
    items: ['#15'], files: ['duvo/store/schema.sql', 'duvo/store/db.py', 'tests/test_store_db.py'],
    map: mapStr('store'),
    quote: `#15 "Schema migrations (versioned, not IF NOT EXISTS). Add a migrate() step keyed on PRAGMA user_version, using PRAGMA table_info-guarded ALTER TABLE to add, on existing DBs: triggered_by + companies_json (runs), agent_log_json (account_runs), and delivery_status (writeback_events, default succeeded for legacy rows so old data isn't mistaken for retryable). _INITED (db.py:36) gates per-process; migration runs on first connect of a process."\nNOTE: this is listed under Backend but is sequenced FIRST in Phase 1 because #1/#3/#6a cannot be TDD-green without the columns. Establish the columns on fresh DBs (schema.sql) AND the versioned migrate() for existing DBs here, as the single owner of schema evolution.`,
    rubric: `- schema.sql: PRAGMA user_version bumped (>1); fresh-DB CREATE TABLE includes triggered_by + companies_json (runs), agent_log_json (account_runs), delivery_status (writeback_events).\n- db.py: migrate() reads PRAGMA user_version, uses PRAGMA table_info to guard each ALTER TABLE (idempotent), runs on first connect of a process (alongside/under the _INITED gate), and bumps user_version when done.\n- Legacy writeback_events rows get delivery_status default 'succeeded' (NOT retryable).\n- Existing-DB upgrade test: open a DB at the OLD schema (no new columns), connect, assert all four columns now exist and user_version advanced; legacy writeback rows default 'succeeded'.\n- db.py still degrades safely elsewhere (_safe unchanged for the public helpers).`,
    testCmd: 'uv run pytest tests/test_store_db.py -q',
    task: `Add the four new columns to fresh-DB schema.sql and bump PRAGMA user_version. Add a versioned migrate() in db.py keyed on PRAGMA user_version with PRAGMA table_info-guarded ALTER TABLEs for existing DBs, defaulting legacy writeback_events.delivery_status to 'succeeded'. Wire migrate() to run once per process on first connect (with/under the _INITED gate). TDD: write the existing-DB upgrade test first (open an old-schema DB, connect, assert columns + user_version + legacy default).`,
  },
  {
    id: 'P1b-runs', title: 'runs.py: triggered_by/companies_json + start_run_strict single-transaction preflight (#2, #4, #5 seed)',
    items: ['#2', '#4', '#5'], files: ['duvo/store/runs.py', 'duvo/store/db.py', 'tests/test_store_runs.py'],
    map: mapStr('store'),
    quote: `#2 "One owner of the full runs insert. start_run() gains a triggered_by column and remains the single writer of the runs row ... The API does not pre-insert a partial row."\n#4 "Strict create-run preflight (one transaction). Add store.start_run_strict(...) that does not swallow errors (unlike db.py:63's _safe) and, in a single transaction, writes both the runs row AND all pending account_runs rows (#5). POST /runs calls it and returns 5xx if it fails ... never a phantom 201 ... The async task is launched only after this preflight commits."\n#5 "Pre-seed all accounts as pending — done inside the #4 preflight transaction (not best-effort inside the run path), so the snapshot is guaranteed present at 201."`,
    rubric: `- start_run() gains triggered_by AND companies_json columns and stays the single writer (keeps ON CONFLICT DO NOTHING idempotency for resume).\n- start_run_strict(): writes the runs row AND every pending account_runs row in ONE transaction (single connection, single commit). It does NOT use _safe — it RAISES on failure (so POST /runs can 5xx). It needs a NON-swallowing transactional executor in db.py (add one; the existing _safe-wrapped helpers stay for best-effort callers).\n- On failure the transaction rolls back: NO runs row and NO account_runs rows persist (test it).\n- Accounts are seeded status='pending'.\n- Tests: start_run_strict commits run+pending atomically; a forced failure raises AND leaves zero rows; companies_json/triggered_by round-trip.`,
    testCmd: 'uv run pytest tests/test_store_runs.py -q',
    task: `Add triggered_by + companies_json to start_run(). Add a non-swallowing transactional executor in db.py (e.g. execute_tx that runs a callback within one connection/transaction and re-raises). Add start_run_strict(*, run_id, run_date, started_at, dry_run, concurrency, model, app_env, triggered_by, companies_json, accounts) that writes the runs row + all pending account_runs in ONE transaction and RAISES on error. TDD first.`,
  },
  {
    id: 'P1c-account_runs', title: 'account_runs.py: reset clears terminal fields (#5) + dry_run=0 baseline filters (#3)',
    items: ['#3', '#5'], files: ['duvo/store/account_runs.py', 'tests/test_store_account_runs.py'],
    map: mapStr('store'),
    quote: `#5 "Resetting an account to pending/running (on resume) clears its terminal fields — upsert_account_run (account_runs.py:22) must null out score/tier/confidence/error/finished_at/signals_*/agent_log_json in the same statement, so a restarted account never shows the previous attempt's stale terminal data."\n#3 "Baselines/skip: last_done_for_domain (diff baseline), done_domains_for_date (--skip-done-today), and the idempotency query JOIN runs and filter dry_run=0. A dry-run can never become a diff baseline, be skipped-as-done, or gate a real send."`,
    rubric: `- upsert_account_run ON CONFLICT ... DO UPDATE now also NULLs: score, tier, confidence, needs_human_research, signals_count, signals_json, score_json, agent_log_json, error, finished_at — in the SAME statement.\n- last_done_for_domain JOINs runs and filters r.dry_run=0 (a persisted dry-run is never a diff baseline).\n- done_domains_for_date JOINs runs and filters r.dry_run=0.\n- Tests: re-upserting a previously-done account to running shows NULL terminal fields; a dry-run row does NOT appear as last_done baseline nor in done_domains_for_date.`,
    testCmd: 'uv run pytest tests/test_store_account_runs.py -q',
    task: `Extend upsert_account_run's DO UPDATE to null every terminal field. Add r.dry_run=0 filters (JOIN runs) to last_done_for_domain and done_domains_for_date. TDD first (stale-field clearing + dry-run isolation from baselines/skip).`,
  },
  {
    id: 'P1d-events', title: 'events.py: simulated action for dry-runs (#3) + delivery lifecycle & run-scoped idempotency (#6a)',
    items: ['#3', '#6a'], files: ['duvo/store/events.py', 'tests/test_store_events.py'],
    map: mapStr('store'),
    quote: `#3 "Write-back events: derive_action() detects the [dry-run] prefix (and/or the run's dry_run flag is threaded into record_event) → action simulated, never alerted/queued/upserted. The idempotency guard (#6a) ignores simulated rows."\n#6a "Scope = (run_id, domain, channel), not domain:channel. ... Delivery lifecycle on the ledger. writeback_events gains a delivery_status (attempting → succeeded / failed). Write attempting BEFORE the Slack/outreach call, update to succeeded/failed AFTER. On resume: suppress only succeeded; retry definite failed; a dangling attempting (crash mid-call) is surfaced for a human decision, never silently re-fired."\n#3 baselines: "the idempotency query JOIN runs and filter dry_run=0."`,
    rubric: `- derive_action / record_event yields action='simulated' for a dry-run (detect '[dry-run]' prefix and/or a dry_run flag threaded into record_event); never alerted/queued/upserted for dry-runs.\n- writeback_events idempotency scope is (run_id, domain, channel) — NOT a forever domain:channel. (UNIQUE(run_id,domain,channel) already exists; the suppression QUERY must be run-scoped.)\n- delivery_status lifecycle: a helper writes 'attempting' before a send and updates to 'succeeded'/'failed' after.\n- A suppression/decision query: for a given run_id, suppress ONLY 'succeeded'; report 'failed' as retryable; surface dangling 'attempting' for human decision; IGNORE 'simulated'; and JOIN runs filtering dry_run=0 so a dry-run never gates a real send.\n- Tests: same run_id 'succeeded' is suppressed; 'failed' is retryable; 'attempting' is surfaced; a fresh run_id is NOT suppressed (re-fires); a 'simulated' row never suppresses a later real send.`,
    testCmd: 'uv run pytest tests/test_store_events.py -q',
    task: `Add action='simulated' for dry-runs in derive_action/record_event. Persist delivery_status (add a set_delivery_status / record-around-the-call helper: 'attempting' then 'succeeded'/'failed'). Add a run-scoped idempotency/decision query (run_id,domain,channel) that suppresses only succeeded, flags failed retryable, surfaces dangling attempting, ignores simulated, and filters runs.dry_run=0. TDD first; cover the fresh-run-id re-fire and dry-run isolation cases explicitly.`,
  },
  {
    id: 'P1e-orchestrator', title: 'orchestrator.run() generalized signature + CLI unchanged + persist/manage_clients decouple + cancel/interrupt + idempotency wiring (#1, #3, #6, #6a, #10 persist)',
    items: ['#1', '#3', '#6', '#6a', '#10'], files: ['duvo/orchestrator.py', 'main.py', 'tests/test_main.py', 'tests/test_orchestrator.py'],
    map: mapStr('orchestrator'),
    quote: `#1 "Generalize run()'s signature ... run(*, run_id, companies, triggered_by, dry_run, test_email, persist, manage_clients, limit=None, concurrency=None, resume_run_id=None, skip_done_today=False, log_level=...). run_id is unambiguous: run_id = resume_run_id or uuid4().hex. CLI (main()): run_id = args.resume_run_id or uuid4().hex, calls load_companies(), passes triggered_by='cli', persist = not dry_run, manage_clients=True. Behavior unchanged — preserves the offline tests (incl. tests/test_main.py:641, dry-run persists nothing on the CLI path). API: ... persist=True (always), manage_clients=False. Persist the run's input ... companies_json ... Resume reconstructs companies from this stored input — never from repo-local companies.csv."\n#3 "Decouple persist from dry_run ... Server: persist=True always ... dry_run now controls only write-back simulation, not persistence."\n#6 "Cancellation / interrupt semantics — surface, don't auto-resume. Add cancelled / interrupted account+run statuses. Handle asyncio.CancelledError at both run and account level (don't blindly mark done in finally)."\n#6a "run-scoped write-back idempotency guard" (wire the #6a query/lifecycle around the actual Slack/outreach calls so a same-run retry is suppressed but a fresh run re-fires).\n#10 "persist each account's agent_log at account completion" (write agent_log_json via the column added in P1a).`,
    rubric: `- run() signature is keyword-only and EXACTLY: run_id, companies, triggered_by, dry_run, test_email, persist, manage_clients, limit=None, concurrency=None, resume_run_id=None, skip_done_today=False, log_level=...; run_id = resume_run_id or uuid4().hex inside run() (no caller run_id racing resume_run_id).\n- CLI main(): run_id = args.resume_run_id or uuid4().hex; calls load_companies(); triggered_by='cli'; persist = not dry_run; manage_clients=True. CLI BEHAVIOR UNCHANGED — tests/test_main.py stays green, ESPECIALLY the dry-run-persists-nothing-on-CLI guarantee (~test_main.py:641).\n- persist is decoupled from dry_run (explicit param); manage_clients=False must NOT close the shared http_client/exa_tool (lifespan owns that); manage_clients=True closes them as today.\n- companies are passed in (not re-read from companies.csv inside run for the API path); companies_json persisted on the runs row via start_run/strict; a persisted dry-run uses action 'simulated' end-to-end.\n- asyncio.CancelledError handled at run + account level → 'cancelled'/'interrupted' statuses, NOT blindly 'done' in finally.\n- The #6a guard is wired around the Slack/outreach calls: same run_id retry suppressed (succeeded), failed retried, dangling attempting surfaced; fresh run_id re-fires.\n- agent_log persisted to agent_log_json at account completion.\n- New behavior covered by tests/test_orchestrator.py; tests/test_main.py UNCHANGED in intent and green.`,
    testCmd: 'uv run pytest tests/test_main.py tests/test_orchestrator.py -q',
    task: `Refactor run() to the exact keyword-only signature in #1. Update main() to preserve CLI behavior exactly (triggered_by='cli', persist=not dry_run, manage_clients=True, load_companies(), run_id=resume_run_id or uuid4().hex). Thread companies in; persist companies_json on the runs row. Decouple persist from dry_run; gate shared-client teardown on manage_clients. Add cancelled/interrupted statuses and handle CancelledError at run+account level. Wire the #6a run-scoped idempotency/delivery-lifecycle (from P1d) around the writeback calls. Persist agent_log_json at completion. TDD: ADD tests/test_orchestrator.py for the NEW behavior; do NOT weaken tests/test_main.py — the CLI guarantee must stay green. Run the full suite mentally but only your testCmd here.`,
  },
]
const p1Results = []
for (const u of P1) p1Results.push(await buildUnit(u, 'Phase 1 — Pipeline refactor (#1–#6a)'))

// Phase 1 boundary gate: the FULL offline suite must be green — especially the unchanged-CLI guarantee.
let p1Suite = await runSuite('suite:phase1', PY, 'Phase 1 — Pipeline refactor (#1–#6a)')
if (p1Suite && !p1Suite.green) {
  log('Phase 1 suite RED — dispatching a fix agent (CLI guarantee is non-negotiable)')
  await agent(
    `${PREAMBLE}\n\nPhase 1 left the offline pytest suite RED. Fix it WITHOUT changing scope and WITHOUT weakening the CLI guarantees in tests/test_main.py (esp. dry-run persists nothing on the CLI path). Failing tail:\n${(p1Suite.output_tail || '').slice(-2000)}\nRun \`${PY}\` until green. You may edit only Phase-1-owned files (orchestrator.py, main.py, duvo/store/*, their tests). NO git mutations.`,
    { label: 'fix:phase1', phase: 'Phase 1 — Pipeline refactor (#1–#6a)', schema: IMPL_SCHEMA })
  p1Suite = await runSuite('suite:phase1-recheck', PY, 'Phase 1 — Pipeline refactor (#1–#6a)')
}
log(`Phase 1 gate: pytest green=${p1Suite && p1Suite.green}`)

// ═══════════════════════════ PHASE 2 — BACKEND FASTAPI (#7–#17, #25) ═══════════════════════════
phase('Phase 2 — Backend FastAPI (#7–#17, #25)')

// 2.0 deps (sequential singleton; owns pyproject.toml + uv.lock). Install may use the network.
const depsUnit = {
  id: 'P2-deps', title: 'Backend dependencies via uv (#25)', items: ['#25'],
  files: ['pyproject.toml', 'uv.lock'], map: '(none)',
  quote: `#25 "Backend (uv add, per the uv-only house rule): uv add fastapi \\"uvicorn[standard]\\" sse-starlette python-multipart authlib itsdangerous (httpx is already a dependency). ... Commit the updated uv.lock." (Per IMPLEMENTATION CONSTRAINTS: update uv.lock as a WORKING-TREE change only — do NOT git commit.)`,
  rubric: `- pyproject.toml dependencies now include fastapi, uvicorn[standard], sse-starlette, python-multipart, authlib, itsdangerous.\n- uv.lock is updated (working-tree change) and consistent — \`uv sync\` / import of fastapi works.\n- NO git commit was made.`,
  testCmd: 'uv run python -c "import fastapi, sse_starlette, authlib, itsdangerous, multipart, uvicorn; print(\'deps ok\')"',
  task: `Run: \`cd ${WORKDIR} && uv add fastapi "uvicorn[standard]" sse-starlette python-multipart authlib itsdangerous\`. This updates pyproject.toml + uv.lock in the working tree. Verify imports with your testCmd. Do NOT git commit anything.`,
}
const p2deps = await buildUnit(depsUnit, 'Phase 2 — Backend FastAPI (#7–#17, #25)')

// 2.1 event bus (new file; prerequisite for core-wiring + SSE)
const eventsBus = {
  id: 'P2-events-bus', title: 'In-process event bus (#9)', items: ['#9'],
  files: ['duvo/infra/events.py', 'tests/test_infra_events.py'], map: mapStr('agent_core'),
  quote: `#9 "In-process event bus (new duvo/infra/events.py, mirrors infra/tracing.py's optional-boundary pattern): Maintains, per run_id, a set of bounded subscriber asyncio.Queues. publish(run_id, event) is a no-op with no subscribers (CLI + offline tests unaffected). On a full queue it drops/coalesces, never awaits the producer (no backpressure). Producer threads run_id + structured context via a contextvar ... Bounded, redacted event payload. Events never carry raw tc.arguments ... uses the existing _short(tc.arguments) truncation plus email masking (reuse tracing._mask). Define a small typed event schema (type, run_id, account, agent, beat, tool, arg_summary, ts)."`,
  rubric: `- per-run_id set of BOUNDED asyncio.Queues; subscribe/unsubscribe API.\n- publish(run_id, event) is a NO-OP when there are no subscribers (assert no error, nothing queued).\n- On a FULL queue, publish drops/coalesces and NEVER awaits the producer (no backpressure) — test that publish returns promptly when a queue is full.\n- A contextvar carries run_id + structured context; per-run_id isolation HOLDS under asyncio.gather (interleaved runs keep separate tags) — unit-tested.\n- Typed event schema fields: type, run_id, account, agent, beat, tool, arg_summary, ts. Payload is _short + email-masked (reuse tracing._mask), NEVER raw arguments.\n- Mirrors the optional-boundary pattern (importable with no subscribers / offline).`,
  testCmd: 'uv run pytest tests/test_infra_events.py -q',
  task: `Create duvo/infra/events.py: per-run_id bounded subscriber asyncio.Queues; subscribe()/unsubscribe(); publish(run_id, event) no-op without subscribers and drop/coalesce on full (never await producer); a contextvar (run_id + context dict) with helpers to set/enrich; a typed event dataclass/TypedDict (type, run_id, account, agent, beat, tool, arg_summary, ts); a make_tool_event helper using agent_core._short + tracing._mask for redaction. TDD first: no-subscriber no-op, bounded-queue drop without blocking, contextvar isolation under asyncio.gather, redaction (never raw args).`,
}
const p2bus = await buildUnit(eventsBus, 'Phase 2 — Backend FastAPI (#7–#17, #25)')

// 2.2 parallel — all independent new/disjoint files (do NOT import each other yet)
const stage21 = [
  {
    id: 'P2-store-queries', title: 'Read-side store query helpers (#12 data layer)', items: ['#12'],
    files: ['duvo/store/queries.py', 'tests/test_store_queries.py'], map: mapStr('store'),
    quote: `#12 "Read endpoints. GET /runs (history), GET /runs/{id} (run + account_runs snapshot), GET /runs/{id}/accounts/{domain} (detail: score_json, signals_json, agent_log_json, and the score/tier diff already computed for writeback_events). New duvo/store/ query helpers, one-file-per-concern."`,
    rubric: `- list_runs() returns run rows for history (ordered, newest first).\n- get_run(run_id) returns the run row + its account_runs snapshot.\n- get_account_detail(run_id, domain) returns score_json, signals_json, agent_log_json + the score/tier diff (prev_score/prev_tier from writeback_events or last_done).\n- Uses the existing db.query_* helpers (offline, safe defaults). Tests cover empty + populated.`,
    testCmd: 'uv run pytest tests/test_store_queries.py -q',
    task: `Create duvo/store/queries.py with list_runs(), get_run(run_id), get_account_detail(run_id, domain) using db.query_one/query_all. TDD first against an in-memory/temp DB seeded via existing store writers.`,
  },
  {
    id: 'P2-jobs', title: 'In-process job registry + done_callback (#8)', items: ['#8'],
    files: ['duvo/api/__init__.py', 'duvo/api/jobs.py', 'tests/test_api_jobs.py'], map: '(none)',
    quote: `#8 "A process-level registry maps run_id → task. Each task gets task.add_done_callback(...) that: removes it from the registry, logs any unhandled exception, and marks the run failed/interrupted if the task died without a clean terminal status — so tasks never leak and exceptions never get swallowed. The registry is also drained on shutdown (#6)."`,
    rubric: `- A process-level registry maps run_id → asyncio.Task (register/get/active/remove).\n- A done_callback: de-registers the task, logs unhandled exceptions, and marks the run failed/interrupted if it died without a clean terminal status.\n- A drain/cancel-all for shutdown.\n- Tests: a crashed task → run marked failed AND de-registered (mock the store mark); a clean task de-registers; drain cancels active tasks. Offline.`,
    testCmd: 'uv run pytest tests/test_api_jobs.py -q',
    task: `Create duvo/api/__init__.py and duvo/api/jobs.py: a registry (dict run_id→Task) with register(run_id, task) that attaches add_done_callback handling de-registration + exception logging + marking run failed/interrupted when it died without a terminal status; plus drain() for shutdown. TDD first.`,
  },
  {
    id: 'P2-auth', title: 'Google SSO + auth guard + real-run authorization gate (#13, #14)', items: ['#13', '#14'],
    files: ['duvo/api/auth.py', 'tests/test_api_auth.py'], map: '(none)',
    quote: `#13 "Auth — Google SSO (OAuth2) ... GET /auth/login → Google consent with state; GET /auth/callback validates the id_token (issuer, audience, expiry, signature) and enforces Workspace hd / allowlist; issues an httpOnly + Secure + SameSite session cookie signed with an env session secret. GET /me, POST /auth/logout. A dependency guards every non-auth route; CSRF protection on state-changing requests. These are tested, not just listed."\n#14 "Authorization guardrail ... a real (non-dry) run ... requires an allowlisted role + an explicit confirmation."`,
    rubric: `- /auth/login redirects to Google consent WITH a state param (CSRF for the OAuth dance).\n- /auth/callback validates id_token issuer, audience, expiry, AND signature; enforces Workspace hd / email allowlist; rejects otherwise.\n- Session cookie is httpOnly + Secure + SameSite, signed with an ENV session secret (itsdangerous).\n- /me and /auth/logout exist; a FastAPI dependency guards every non-auth route.\n- CSRF protection on state-changing requests.\n- A real-run authorization check: requires an allowlisted role + explicit confirmation flag; a dry-run does not.\n- Tests mock Google token verification and assert state, CSRF, cookie flags (httpOnly/Secure/SameSite), allowlist enforcement, and the real-run gate. Offline (no live Google).`,
    testCmd: 'uv run pytest tests/test_api_auth.py -q',
    task: `Create duvo/api/auth.py (authlib for OAuth, itsdangerous for the signed cookie): login (state), callback (verify issuer/aud/exp/signature + hd/allowlist, set httpOnly+Secure+SameSite cookie), /me, /auth/logout, a current-user dependency guarding routes, CSRF on state-changing requests, and a require_real_run_authorization(user, confirm) gate (allowlisted role + explicit confirmation). Config via env in config.py is allowed ONLY if you also list config.py in files — otherwise read os.environ locally with safe defaults; prefer adding keys lazily. TDD first with mocked token verification.`,
  },
]
// auth may need config keys — keep config edits out of parallel stage to avoid clashing with core-wiring; auth reads env lazily.
const p2stage21 = await parallel(stage21.map((u) => () => buildUnit(u, 'Phase 2 — Backend FastAPI (#7–#17, #25)')))

// 2.3 parallel — core-wiring (shared agent files; needs events bus) + runs-endpoints (new file; needs jobs/auth/store-queries/events bus). Disjoint file sets.
const stage22 = [
  {
    id: 'P2-core-wiring', title: 'Event context enrichment + tool-event publish + global account semaphore (#9 wiring, #17)', items: ['#9', '#17'],
    files: ['duvo/orchestrator.py', 'duvo/agent_core.py', 'duvo/agents/scouts/scouts.py', 'duvo/agents/analyst/analyst.py', 'duvo/agents/router/router.py', 'duvo/config.py', 'tests/test_event_wiring.py'],
    map: mapStr('orchestrator') + '\n' + mapStr('agent_core'),
    quote: `#9 "Context is enriched where the metadata lives ... _process_account sets run_id+account; run_scout adds agent='scout'+beat; run_analyst/run_router add their role. agent_core.run_agent publishes a tool event at its existing tool-call site (agent_core.py:91). asyncio.gather copies context per task, so tags stay isolated across interleaved runs."\n#17 "Cross-run rate cap (decided). Add a process-wide global account semaphore (DUVO_GLOBAL_MAX_ACCOUNTS, default e.g. 8) bounding total concurrent accounts across ALL runs, on top of each run's per-run Semaphore(concurrency)."`,
    rubric: `- _process_account sets the events contextvar run_id+account; run_scout adds agent='scout'+beat; run_analyst/run_router add their role — context enriched WHERE the metadata lives. NO agent function SIGNATURE changes (contextvar only).\n- agent_core.run_agent publishes a 'tool' event at the existing tool-call site (~line 91) using the redacted _short + masked payload — NEVER raw tc.arguments.\n- contextvar isolation holds under asyncio.gather (interleaved runs keep separate tags).\n- DUVO_GLOBAL_MAX_ACCOUNTS in config.py (default 8); a process-wide global semaphore bounds TOTAL concurrent accounts across ALL runs, on TOP of the per-run Semaphore(concurrency). The per-run semaphore is preserved.\n- publish stays a no-op with no subscribers (CLI/offline unaffected) — existing tests still green.\n- Do NOT change bounded-agency guards/scoring/write-back semantics.`,
    testCmd: 'uv run pytest tests/test_event_wiring.py tests/test_agent_core.py tests/test_scouts.py -q',
    task: `Wire the event bus: set/enrich the contextvar in _process_account (run_id+account), run_scout (agent='scout'+beat), run_analyst/run_router (role); publish a redacted 'tool' event from agent_core.run_agent at the tool-call site. Add DUVO_GLOBAL_MAX_ACCOUNTS (default 8) to config.py and a process-wide global semaphore acquired around each account in addition to the per-run semaphore. NO agent signature changes; contextvar only. TDD first (tests/test_event_wiring.py: enrichment, gather isolation, global-semaphore bound). Keep existing tests green.`,
  },
  {
    id: 'P2-runs-endpoints', title: 'POST /runs + SSE stream + read endpoints (#8 endpoint, #10 SSE semantics, #11, #12)', items: ['#8', '#10', '#11', '#12'],
    files: ['duvo/api/routes_runs.py', 'tests/test_api_routes_runs.py', 'tests/test_api_sse.py'],
    map: mapStr('store'),
    quote: `#8 "POST /runs: authenticate → authorize (real-run gate, #14) → validate request → generate run_id → start_run_strict() → asyncio.create_task(run(...)) → 201 {run_id}."\n#11 "SSE endpoint. GET /runs/{id}/stream → text/event-stream (sse-starlette): register a bounded subscriber queue, flush the DB snapshot (#10), stream account / tool / status events until terminal status, then close. Heartbeat comments + Cache-Control: no-cache + disabled proxy buffering."\n#12 read endpoints (GET /runs, GET /runs/{id}, GET /runs/{id}/accounts/{domain}).\n#10 "the SSE snapshot on (re)connect replays account status (pending/running/terminal) + completed accounts' agent_log_json — it does NOT replay an in-flight account's earlier tool lines."`,
    rubric: `- POST /runs: authenticate → real-run authorization gate (#14, allowlisted role + explicit confirmation for non-dry) → validate (incl. CSV size/columns/row cap) → run_id=uuid4().hex → start_run_strict() (commits run+pending BEFORE launching) → asyncio.create_task(run(..., persist=True, manage_clients=False, triggered_by=user.email)) registered in the job registry → 201 {run_id}. start_run_strict raising ⇒ 5xx with NO task and NO rows.\n- GET /runs, GET /runs/{id} (run + account_runs snapshot), GET /runs/{id}/accounts/{domain} (detail incl. diff) via the store query helpers.\n- GET /runs/{id}/stream: text/event-stream; registers a bounded subscriber queue; flushes the DB snapshot (status + completed agent_log_json, NOT in-flight tool replay); streams account/tool/status events to terminal then closes; heartbeat + Cache-Control: no-cache + disabled buffering headers.\n- Tests use FastAPI TestClient / httpx.ASGITransport with run() MOCKED. SSE test asserts ordering + snapshot replay + terminal close on a fake stream. start_run_strict→5xx leaves no task/rows. Offline.`,
    testCmd: 'uv run pytest tests/test_api_routes_runs.py tests/test_api_sse.py -q',
    task: `Create duvo/api/routes_runs.py (an APIRouter) with POST /runs (auth + real-run gate + CSV validation/row-cap + start_run_strict + create_task + registry + 201), GET /runs, GET /runs/{id}, GET /runs/{id}/accounts/{domain}, and GET /runs/{id}/stream (sse-starlette EventSourceResponse: subscribe bounded queue, flush snapshot, stream to terminal, heartbeat + no-cache + no-buffering). Import the bus (P2-events-bus), jobs registry (P2-jobs), auth gate (P2-auth), store queries (P2-store-queries), and orchestrator.run. TDD first with run() mocked; cover the 5xx-leaves-nothing path and the SSE snapshot/terminal-close path on a fake stream.`,
  },
]
const p2stage22 = await parallel(stage22.map((u) => () => buildUnit(u, 'Phase 2 — Backend FastAPI (#7–#17, #25)')))

// 2.4 app + lifespan + static mount (needs routes/auth/jobs/events) — sequential, owns app.py
const appUnit = {
  id: 'P2-app', title: 'FastAPI app + lifespan (startup sweep/shutdown drain) + SPA static mount (#7, #6 lifespan, #16)', items: ['#7', '#6', '#16'],
  files: ['duvo/api/app.py', 'tests/test_api_app.py'], map: mapStr('orchestrator'),
  quote: `#7 "App + lifespan. duvo/api/app.py. The lifespan context: at startup calls configure_logging() and tracing.init_tracing() once ... and runs the interrupted-run sweep (#6); at shutdown drains active run tasks, then calls http_client.aclose(), exa_tool.aclose(). Exa stays lazy — the lifespan does NOT eagerly construct it ... Tracing is flushed per-run-completion."\n#6 "On app startup, mark stale running runs as interrupted and surface them ... resume is human-initiated, NOT automatic."\n#16 "Serve the SPA. Mount the Vite build via StaticFiles with SPA fallback to index.html → one deployable."`,
  rubric: `- duvo/api/app.py builds the FastAPI app with a lifespan that at STARTUP calls configure_logging() + tracing.init_tracing() ONCE and runs the interrupted-run sweep (stale 'running' runs → 'interrupted'); at SHUTDOWN drains active tasks (jobs registry) THEN http_client.aclose() + exa_tool.aclose(). Exa is NOT eagerly constructed in lifespan (stays lazy — no EXA_API_KEY required to boot).\n- Routers from routes_runs + auth are mounted; the auth guard dependency protects non-auth routes.\n- StaticFiles mount serves the Vite build with SPA fallback to index.html (tolerates a missing build dir at test time).\n- Tests (TestClient): startup sweep marks a stale running run interrupted; shutdown drains tasks and closes shared clients; lazy-Exa boot (no key) works; SPA fallback route returns index. Offline.`,
  testCmd: 'uv run pytest tests/test_api_app.py -q',
  task: `Create duvo/api/app.py: create_app() with an async lifespan (startup: configure_logging + tracing.init_tracing once + interrupted-run sweep via a store helper; shutdown: jobs.drain() then http_client.aclose() + exa_tool.aclose()). Mount auth + routes_runs routers and the auth-guard dependency. Mount StaticFiles for the frontend build dir with SPA fallback to index.html (guard against a missing dir). Add a store helper for the stale-running→interrupted sweep if needed (you may add it to duvo/store/runs.py — if so, ADD runs.py to your files list and keep its tests green). TDD first.`,
}
const p2app = await buildUnit(appUnit, 'Phase 2 — Backend FastAPI (#7–#17, #25)')

// Phase 2 boundary gate
let p2Suite = await runSuite('suite:phase2', PY, 'Phase 2 — Backend FastAPI (#7–#17, #25)')
if (p2Suite && !p2Suite.green) {
  log('Phase 2 suite RED — dispatching a fix agent (scope-preserving)')
  await agent(
    `${PREAMBLE}\n\nPhase 2 left the offline pytest suite RED. Fix WITHOUT scope creep and WITHOUT weakening Phase-1 CLI guarantees. Failing tail:\n${(p2Suite.output_tail || '').slice(-2000)}\nRun \`${PY}\` until green. Edit only backend files already created in Phases 1–2 and their tests. NO git mutations.`,
    { label: 'fix:phase2', phase: 'Phase 2 — Backend FastAPI (#7–#17, #25)', schema: IMPL_SCHEMA })
  p2Suite = await runSuite('suite:phase2-recheck', PY, 'Phase 2 — Backend FastAPI (#7–#17, #25)')
}
log(`Phase 2 gate: pytest green=${p2Suite && p2Suite.green}`)

// ═══════════════════════════ PHASE 3 — FRONTEND SPA (#18–#22, #24, #26) ═══════════════════════════
phase('Phase 3 — Frontend SPA (#18–#22, #24, #26)')

// 3.1 scaffold (sequential foundation; pnpm install may use network; vitest offline)
const scaffold = {
  id: 'P3-scaffold', title: 'Vite+React+TS+Tailwind+shadcn/ui+TanStack Query+Router scaffold under ./frontend with pnpm (#26)', items: ['#26'],
  files: ['frontend/'], map: '(new project)',
  quote: `#26 "Frontend package manager: pnpm, with a committed pnpm-lock.yaml under frontend/. Vite + React + TypeScript scaffold; Tailwind + shadcn/ui; TanStack Query + React Router."\nStack line: "Vite + React + TypeScript, TanStack Query, React Router, Tailwind + shadcn/ui, native EventSource for SSE."\n#24 "Frontend: Vitest + Testing Library ... No network in CI."`,
  rubric: `- ./frontend is a Vite + React + TypeScript project managed by pnpm with a committed pnpm-lock.yaml (working-tree file, NOT git-committed).\n- Tailwind configured; shadcn/ui initialized with base primitives (button, dialog, input, etc.); TanStack Query (QueryClientProvider) + React Router wired in src/main.tsx / src/App.tsx.\n- Vitest + Testing Library configured; \`pnpm test\` runs vitest in NON-watch (run) mode and passes a trivial smoke test OFFLINE.\n- A typed API client (src/lib/api.ts) + shared types and a native EventSource helper stub exist for features to import.\n- A trivial smoke test passes: \`cd frontend && pnpm test\`.`,
  testCmd: 'cd frontend && pnpm test',
  task: `Scaffold ./frontend with pnpm: Vite + React + TS; Tailwind + shadcn/ui (init + base primitives); TanStack Query + React Router in main.tsx/App.tsx; Vitest + Testing Library with "test": "vitest run" so \`pnpm test\` is non-watch and offline; src/lib/api.ts (typed fetch client + types matching the backend run/account shapes) and an EventSource helper. Run pnpm install (network OK) to produce pnpm-lock.yaml. Add ONE smoke test and confirm \`pnpm test\` is green. Do NOT git commit. The scope gate allows the whole ./frontend tree for this unit only.`,
}
const p3scaffold = await buildUnit(scaffold, 'Phase 3 — Frontend SPA (#18–#22, #24, #26)')

// 3.2 cross-cutting shared UI (confirm dialog used by new-run) — before features
const crosscutting = {
  id: 'P3-crosscutting', title: 'Cross-cutting UI: skeletons, empty states, error toasts, confirm dialog (#22)', items: ['#22'],
  files: ['frontend/src/components/common/'], map: '(new)',
  quote: `#22 "Cross-cutting: loading skeletons, empty states, error toasts, confirm dialog."`,
  rubric: `- Reusable LoadingSkeleton, EmptyState, error Toast (or toast hook), and a ConfirmDialog component exist under frontend/src/components/common/.\n- ConfirmDialog is generic (title/description/onConfirm/onCancel) so NewRunForm can gate a real run with it.\n- Vitest covers ConfirmDialog (confirm/cancel callbacks) and EmptyState. Offline.`,
  testCmd: 'cd frontend && pnpm test src/components/common',
  task: `Create frontend/src/components/common/: LoadingSkeleton, EmptyState, Toast/useToast, ConfirmDialog (generic). TDD with Vitest first. Edit ONLY files under frontend/src/components/common/.`,
}
const p3cross = await buildUnit(crosscutting, 'Phase 3 — Frontend SPA (#18–#22, #24, #26)')

// 3.3 parallel feature groups — disjoint folders
const stage33 = [
  {
    id: 'P3-shell', title: 'App shell: AuthGuard, LoginScreen, AppLayout, Toast, ErrorBoundary (#18)', items: ['#18'],
    files: ['frontend/src/components/shell/', 'frontend/src/auth/'], map: '(new)',
    quote: `#18 "App shell: AuthGuard, LoginScreen (Google button), AppLayout (top nav + user menu + sign out), Toast, ErrorBoundary."`,
    rubric: `- AuthGuard gates routes on session (redirects unauthenticated to LoginScreen), reading /me.\n- LoginScreen has the Google sign-in button (→ /auth/login).\n- AppLayout: top nav + user menu + sign out (→ /auth/logout).\n- ErrorBoundary catches render errors. Vitest covers AuthGuard (authed vs not) + ErrorBoundary fallback. Offline (mock fetch/router).`,
    testCmd: 'cd frontend && pnpm test src/components/shell src/auth',
    task: `Create the app shell under frontend/src/components/shell/ and frontend/src/auth/ (AuthGuard, LoginScreen, AppLayout, ErrorBoundary; reuse common Toast). TDD with Vitest, mocking /me and router. Edit ONLY those two folders.`,
  },
  {
    id: 'P3-newrun', title: 'New Run form + confirm gate (#19)', items: ['#19'],
    files: ['frontend/src/features/new-run/'], map: '(new)',
    quote: `#19 "New Run: NewRunForm = FileDropzone (companies.csv or repo default), ConcurrencyInput, DryRunToggle (default ON), SkipDoneTodayToggle, TestEmailInput, StartRunButton → useStartRun → POST /runs. A confirm dialog gates a real (non-dry) run. Navigate to the live run view on success."`,
    rubric: `- NewRunForm composes FileDropzone, ConcurrencyInput, DryRunToggle (DEFAULT ON), SkipDoneTodayToggle, TestEmailInput, StartRunButton.\n- useStartRun posts to /runs (TanStack Query mutation).\n- Toggling OFF dry-run (a real run) and submitting triggers the ConfirmDialog (from common); a dry-run submit does NOT.\n- On success navigates to the live run view.\n- Vitest (#24): asserts DryRunToggle defaults ON; the real-run confirm dialog appears; dry-run skips it. Offline (mock useStartRun / fetch).`,
    testCmd: 'cd frontend && pnpm test src/features/new-run',
    task: `Create frontend/src/features/new-run/ (NewRunForm + subcomponents + useStartRun). DryRunToggle default ON; real-run submit gated by the common ConfirmDialog. TDD with Vitest first incl. the real-run-confirm test. Edit ONLY this folder.`,
  },
  {
    id: 'P3-liverun', title: 'Live Run view + useRunStream (#20)', items: ['#20'],
    files: ['frontend/src/features/live-run/'], map: '(new)',
    quote: `#20 "Live Run view (centerpiece): RunHeader (status badge incl. interrupted/cancelled, ProgressBar done/total, ElapsedTimer, triggered_by, model/env tags) · AccountsTable of AccountRow (live status pending→running→terminal, ScoreBadge, TierBadge, confidence, signal count) · ToolCallFeed — auto-scroll stream of account · agent:beat · tool(args) from SSE tool events, filterable. useRunStream(runId) wraps EventSource, replays the snapshot, reconnects on drop, and falls back to GET /runs/{id} polling if SSE fails."`,
    rubric: `- RunHeader shows status badge (incl. interrupted/cancelled), ProgressBar (done/total), ElapsedTimer, triggered_by, model/env tags.\n- AccountsTable/AccountRow render live status pending→running→terminal with ScoreBadge, TierBadge, confidence, signal count.\n- ToolCallFeed auto-scrolls account · agent:beat · tool(args) from SSE tool events and is filterable.\n- useRunStream wraps native EventSource, replays the snapshot, reconnects on drop, and FALLS BACK to GET /runs/{id} polling when SSE fails.\n- Vitest (#24): AccountRow (status/badges) + ToolCallFeed (renders/filters feed lines) with useRunStream MOCKED. Offline.`,
    testCmd: 'cd frontend && pnpm test src/features/live-run',
    task: `Create frontend/src/features/live-run/ (RunHeader, ProgressBar, ElapsedTimer, AccountsTable, AccountRow, ScoreBadge, TierBadge, ToolCallFeed, useRunStream with EventSource + snapshot replay + reconnect + polling fallback). TDD with Vitest first for AccountRow + ToolCallFeed (mock useRunStream). Edit ONLY this folder.`,
  },
  {
    id: 'P3-drilldown', title: 'Account drill-down drawer (#21)', items: ['#21'],
    files: ['frontend/src/features/account-detail/'], map: '(new)',
    quote: `#21 "Account drill-down: AccountDetailDrawer = ScoreCard (why_fit/why_not), SignalList (source links, dates, type), OutreachDraftPreview, DiffBadge (▲+2 vs last run), AgentLogViewer — from GET /runs/{id}/accounts/{domain}."`,
    rubric: `- AccountDetailDrawer composes ScoreCard (why_fit/why_not), SignalList (source links, dates, type), OutreachDraftPreview, DiffBadge (e.g. ▲+2 vs last run), AgentLogViewer.\n- Data from GET /runs/{id}/accounts/{domain} via a TanStack Query hook.\n- Vitest covers ScoreCard + DiffBadge rendering. Offline (mock fetch).`,
    testCmd: 'cd frontend && pnpm test src/features/account-detail',
    task: `Create frontend/src/features/account-detail/ (AccountDetailDrawer + ScoreCard, SignalList, OutreachDraftPreview, DiffBadge, AgentLogViewer + a useAccountDetail query hook). TDD with Vitest first. Edit ONLY this folder.`,
  },
]
const p3stage33 = await parallel(stage33.map((u) => () => buildUnit(u, 'Phase 3 — Frontend SPA (#18–#22, #24, #26)')))

// Phase 3 boundary gate (vitest)
let p3Suite = await runSuite('suite:phase3-vitest', VITEST, 'Phase 3 — Frontend SPA (#18–#22, #24, #26)')
if (p3Suite && !p3Suite.green) {
  log('Phase 3 vitest RED — dispatching a fix agent')
  await agent(
    `${PREAMBLE}\n\nPhase 3 left the Vitest suite RED. Fix WITHOUT scope creep. Failing tail:\n${(p3Suite.output_tail || '').slice(-2000)}\nRun \`${VITEST}\` until green. Edit only files under ./frontend. NO git mutations.`,
    { label: 'fix:phase3', phase: 'Phase 3 — Frontend SPA (#18–#22, #24, #26)', schema: IMPL_SCHEMA })
  p3Suite = await runSuite('suite:phase3-recheck', VITEST, 'Phase 3 — Frontend SPA (#18–#22, #24, #26)')
}
log(`Phase 3 gate: vitest green=${p3Suite && p3Suite.green}`)

// ═══════════════════════════ PHASE 4 — ADVERSARIAL CLEAN-PASS + COMPLETENESS + GIT-CLEAN PROOF ═══════════════════════════
phase('Phase 4 — Adversarial clean-pass + completeness + git-clean proof')

const ITEM_GROUPS = [
  { key: 'pipeline', items: '#1, #2, #3, #4, #5, #6, #6a', files: 'duvo/orchestrator.py main.py duvo/store/*.py duvo/store/schema.sql tests/test_main.py tests/test_orchestrator.py tests/test_store_*.py' },
  { key: 'backend-core', items: '#7, #8, #9, #16, #17, #25', files: 'duvo/api/app.py duvo/api/jobs.py duvo/infra/events.py duvo/agent_core.py duvo/config.py pyproject.toml uv.lock' },
  { key: 'backend-http', items: '#10, #11, #12, #13, #14, #15', files: 'duvo/api/routes_runs.py duvo/api/auth.py duvo/store/queries.py duvo/store/db.py' },
  { key: 'frontend', items: '#18, #19, #20, #21, #22, #24, #26', files: './frontend' },
  { key: 'tests', items: '#23, #24', files: 'tests/ ./frontend' },
]

let round = 0
let gaps = []
const maxRounds = 3
do {
  round++
  log(`Phase 4 round ${round}: full suites + fresh adversarial skeptics`)
  // Full suites + fresh per-group skeptics in parallel.
  const checks = await parallel([
    () => runSuite(`suite:pytest-r${round}`, PY, 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof'),
    () => runSuite(`suite:vitest-r${round}`, VITEST, 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof'),
    ...ITEM_GROUPS.map((g) => () => agent(
      `${PREAMBLE}\n\nYou are a FRESH adversarial verifier (round ${round}). REFUTE that plan items ${g.items} are fully + correctly implemented. Default to NOT-satisfied. Read the plan items verbatim, inspect \`cd ${WORKDIR} && git --no-pager diff -- ${g.files}\` and the relevant tests, and run the targeted tests. For each item, decide met=true/false with file:line / test evidence. Report every UNMET or partially-met item as a gap with a precise, actionable description (what's missing + which file/test).`,
      { label: `verify:${g.key}-r${round}`, phase: 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof', schema: CRITIC_SCHEMA })),
  ])
  const pytestR = checks[0], vitestR = checks[1]
  const skeptics = checks.slice(2).filter(Boolean)
  gaps = skeptics.flatMap((s) => (s.silently_skipped || []).concat((s.items || []).filter((i) => i.status && i.status.toLowerCase() !== 'done').map((i) => `${i.item}: ${i.status} — ${i.evidence}`)))
  const suitesGreen = pytestR && pytestR.green && vitestR && vitestR.green
  log(`Round ${round}: pytest=${pytestR && pytestR.green} vitest=${vitestR && vitestR.green} gaps=${gaps.length}`)

  if (suitesGreen && gaps.length === 0) { log('Clean pass: suites green + no new gaps'); break }
  if (round >= maxRounds) { log(`Reached maxRounds=${maxRounds} with ${gaps.length} residual gap(s)`); break }

  // Fix round: one fresh implementer per gap-bearing group (serialized to avoid shared-file clashes).
  const failingGroups = ITEM_GROUPS.filter((g) => gaps.some((gp) => g.items.split(', ').some((it) => String(gp).includes(it))) || !suitesGreen)
  for (const g of failingGroups) {
    await agent(
      `${PREAMBLE}\n\nFIX ROUND ${round}. Close these gaps for plan items ${g.items} with TDD (failing test first). Gaps + suite failures:\n${JSON.stringify(gaps, null, 2).slice(0, 3000)}\nSuite tails:\npytest: ${(pytestR && pytestR.output_tail || '').slice(-1200)}\nvitest: ${(vitestR && vitestR.output_tail || '').slice(-1200)}\nEdit only files in scope for this group (${g.files}). Run the relevant suite (\`${PY}\` and/or \`${VITEST}\`) until green. NO scope creep, NO git mutations.`,
      { label: `fix:${g.key}-r${round}`, phase: 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof', schema: IMPL_SCHEMA })
  }
} while (round < maxRounds)

// 4b — completeness critic over #1–#26
const critic = await agent(
  `${PREAMBLE}\n\nCOMPLETENESS CRITIC. Walk EVERY plan item #1 through #26. For each, determine has_test (a real offline test exists) AND has_diff (a real working-tree change exists) by inspecting \`cd ${WORKDIR} && git --no-pager diff\` (and untracked files via git status) and the test suites. Flag in silently_skipped any item with NO test AND NO diff (silently skipped) or clearly partial. Be precise: cite the file/test or its absence. Do NOT edit anything.`,
  { label: 'completeness-critic', phase: 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof', schema: CRITIC_SCHEMA })

// If the critic found silently-skipped items, do one final implement→verify round on them.
if (critic && critic.silently_skipped && critic.silently_skipped.length) {
  log(`Completeness critic flagged ${critic.silently_skipped.length} item(s); final fix round`)
  await agent(
    `${PREAMBLE}\n\nFINAL completeness fix. These plan items were flagged as silently skipped (no test AND/OR no diff): ${JSON.stringify(critic.silently_skipped)}. Implement each with TDD in its correct in-scope files, run the relevant suite to green. NO scope creep, NO git mutations.`,
    { label: 'fix:completeness', phase: 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof', schema: IMPL_SCHEMA })
}

// 4 — final full suites
const finalPytest = await runSuite('suite:final-pytest', PY, 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof')
const finalVitest = await runSuite('suite:final-vitest', VITEST, 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof')

// 4c — git-clean proof
const proof = await agent(
  `${PREAMBLE}\n\nFINAL GIT-CLEAN PROOF. Run (read-only; NEVER mutate): \`cd ${WORKDIR} && git status\` and \`git --no-pager log --oneline -10\` and \`git worktree list\` and \`git branch --show-current\`. ASSERT the working tree is DIRTY with ZERO new commits (newest commit MUST still be ${BASELINE_SHORT} / ${BASELINE}) and NO new branches/worktrees. If ANY commit exists beyond baseline, that is a FAILURE to REPORT (commits_made=true) — do NOT undo it. Paste the tails.`,
  { label: 'git-clean-proof', phase: 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof', schema: PROOF_SCHEMA, model: 'sonnet' })

// ───────────────────────── final synthesis ─────────────────────────
const unitResults = [...p1Results, p2deps, p2bus, ...p2stage21, ...p2stage22, p2app, p3scaffold, p3cross, ...p3stage33].filter(Boolean)
const report = await agent(
  `${PREAMBLE}\n\nWrite the FINAL human-facing report (markdown) for this build. Use ONLY the evidence below — do not edit code, do not run git mutations.\n\nUNIT RESULTS:\n${JSON.stringify(unitResults.map((r) => ({ unit: r.unit, items: r.plan_items, status: r.status, attempts: r.attempts, files: r.impl && r.impl.files_changed, reason: r.reason })), null, 1).slice(0, 9000)}\n\nCOMPLETENESS CRITIC:\n${JSON.stringify(critic, null, 1).slice(0, 3000)}\n\nFINAL PYTEST: ${JSON.stringify(finalPytest)}\nFINAL VITEST: ${JSON.stringify(finalVitest)}\nGIT-CLEAN PROOF: ${JSON.stringify(proof)}\n\nProduce a report with: (1) a table of plan items #1–#26 each marked done / blocked-with-reason; (2) the files changed (all uncommitted); (3) final pytest + vitest results; (4) the git-clean proof (dirty tree, zero commits). Be honest — if something is blocked, say so with the reason.`,
  { label: 'final-report', phase: 'Phase 4 — Adversarial clean-pass + completeness + git-clean proof' })

return {
  report,
  finalPytest,
  finalVitest,
  proof,
  units: unitResults.map((r) => ({ unit: r.unit, items: r.plan_items, status: r.status, attempts: r.attempts })),
  completeness: critic,
  rounds: round,
}
