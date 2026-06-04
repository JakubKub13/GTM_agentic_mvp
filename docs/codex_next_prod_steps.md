# Codex Next Production Steps

This is a pragmatic production-readiness checklist for `duvo-signal-loop`. It avoids heavy platform work and focuses on what would matter before running real scheduled batches against live Exa, LLM, CRM, Slack, and outreach systems.

## 1. Add durable run state and idempotency

The current pipeline is one-shot, and some write-backs create records without a durable local record of what happened. Add a small SQLite or Postgres-backed store for `runs`, `account_runs`, and `writeback_events`.

Implementation:
- Generate a `run_id` at the start of each batch.
- Store each account's status, score, errors, and write-back results.
- Use `domain + run_id` or `domain + campaign` as idempotency keys.
- For CRM writes, search/update by domain before creating new records.

## 2. Preserve failed accounts in reports

Failed accounts currently return `None` and are filtered out of the final results. That makes production runs look cleaner than they really are.

Implementation:
- Return an explicit failed account result instead of `None`.
- Capture `step`, `error_type`, and `message`.
- Render failed accounts in the HTML report.
- Add a retry mode that reruns only failed accounts from a previous `run_id`.

## 3. Add production tracing

Console logs are useful, but they are not enough to debug agent behavior in production. Langfuse would fit well because the central agent loop gives one clean instrumentation point.

Implementation:
- Add one trace per account.
- Add spans for each LLM call, tool call, Exa search, analyst guard, and write-back.
- Attach model, latency, token usage, sanitized inputs, sanitized outputs, and final routing decision.
- Keep tracing optional behind env vars so local tests and dry runs stay simple.

## 4. Add evals

The unit tests are strong, but they verify code behavior rather than agent quality. Production needs regression checks for scoring, grounding, and routing decisions.

Implementation:
- Create a small golden dataset of companies, signals, expected tier, confidence, and expected router actions.
- Evaluate source validity, score/tier accuracy, grounded outreach, hallucination rate, and refusal/send correctness.
- Run evals before prompt/model changes.
- Track eval results over time, ideally alongside Langfuse traces.

## 5. Strengthen evidence validation

Signal quality is still partly prompt-dependent. A signal with an empty or malformed URL can pass through, and dates are strings rather than validated dates.

Implementation:
- Reject or downgrade signals with empty `source_url`.
- Parse `published_date` into a real date type or a validated ISO string.
- Drop clearly stale evidence or mark it as weak.
- Include evidence-quality flags in the analyst input and final report.

## 6. Track cost and enforce budgets

The neutral LLM response does not expose token usage, so the orchestrator cannot show or enforce cost.

Implementation:
- Add usage fields to the provider-neutral `LLMResponse`.
- Extract token usage from LiteLLM responses.
- Aggregate tokens and estimated cost per account and per run.
- Add optional env limits such as `MAX_RUN_COST_USD` and `MAX_ACCOUNT_COST_USD`.

## 7. Add provider-specific rate limits

Account-level concurrency exists, but there is no specific throttle for LLM, Exa, CRM, Slack, or outreach providers.

Implementation:
- Add lightweight async semaphores or rate limiters per provider.
- Keep current retry logic, but avoid creating avoidable 429s.
- Configure limits through env vars.
- Log when work waits on a provider throttle.

## 8. Add operational metrics and alerting

The app logs events, but production operators need aggregate health signals.

Implementation:
- Emit structured JSON logs with `run_id`, `company_domain`, `step`, `provider`, and `status`.
- Track failure rate, retry count, latency, account throughput, token cost, and write-back outcomes.
- Alert on high failure rate, repeated provider errors, or zero successful accounts.

## 9. Codify CI and quality gates

There is strong local test coverage, but no visible CI workflow. Type-checking is also loose because missing imports are suppressed in `pyrightconfig.json`.

Implementation:
- Add a simple CI job that runs `uv sync --locked`, `ruff check`, `ruff format --check`, type checking, and `pytest`.
- Tighten type checking gradually rather than all at once.
- Keep external dependencies mocked in CI.

## 10. Add a minimal deployment story

The current project is a CLI demo. Production needs a repeatable way to run it with secrets and scheduling.

Implementation:
- Add a minimal Dockerfile or scheduled job definition.
- Load secrets from the runtime platform, not local `.env`.
- Add a `doctor` command that validates required config before a live run.
- Document dry-run, limited live run, and full scheduled run procedures.

## 11. Define privacy and retention rules

Future traces and reports may contain prompts, account data, emails, and generated outreach. That needs a simple policy before production.

Implementation:
- Centralize redaction for logs and traces.
- Avoid storing full prompt/tool payloads unless explicitly enabled.
- Store reports in a restricted location.
- Define retention for traces, reports, and run state.

## 12. Record prompt, model, and config provenance

Reports should explain what code, model, prompts, and config produced a decision.

Implementation:
- Store `git_sha`, `LLM_MODEL`, provider names, prompt hashes, and key config values in run metadata.
- Include that metadata in the HTML report and trace metadata.
- Use it to compare runs after prompt, model, or config changes.

## What not to add yet

Avoid microservices, Kubernetes, a full admin UI, queues, or complex approval workflows until the simple scheduled pipeline proves useful. The highest-leverage next work is tracing, evals, durable run state, idempotent write-backs, evidence validation, budget tracking, and CI.
