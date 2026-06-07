PRAGMA user_version = 2;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_date TEXT,
    started_at TEXT,
    finished_at TEXT,
    dry_run INTEGER,
    concurrency INTEGER,
    model TEXT,
    app_env TEXT,
    accounts_total INTEGER,
    accounts_succeeded INTEGER,
    accounts_failed INTEGER,
    status TEXT,
    triggered_by TEXT,          -- who launched the run ('cli' | <user.email>) (#2)
    companies_json TEXT         -- exact launch input for faithful resume (#1/#15)
);

CREATE TABLE IF NOT EXISTS account_runs (
    run_id TEXT,
    domain TEXT,
    company_name TEXT,
    country TEXT,
    status TEXT,                 -- pending | running | done | failed | cancelled | interrupted
    score INTEGER,
    tier TEXT,
    confidence TEXT,
    needs_human_research INTEGER,
    signals_count INTEGER,
    signals_json TEXT,
    score_json TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    agent_log_json TEXT,        -- persisted per-account agent tool log (#10)
    PRIMARY KEY (run_id, domain)
);

CREATE INDEX IF NOT EXISTS ix_account_runs_domain_finished
    ON account_runs (domain, finished_at);

CREATE TABLE IF NOT EXISTS writeback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    domain TEXT,
    channel TEXT,               -- crm | slack | outreach
    provider TEXT,
    action TEXT,                -- upserted | alerted | queued | refused | failed | skipped | simulated
    changed INTEGER,            -- 1 if score/tier differs from prior run, else 0
    prev_score INTEGER,
    prev_tier TEXT,
    status_text TEXT,
    idempotency_key TEXT,
    created_at TEXT,
    -- delivery lifecycle: attempting -> succeeded/failed; legacy rows default 'succeeded' (#6a/#15)
    delivery_status TEXT DEFAULT 'succeeded',
    UNIQUE (run_id, domain, channel)
);
