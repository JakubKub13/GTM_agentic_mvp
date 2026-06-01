# duvo-signal-loop Implementation Plan

> **For agentic workers:** This plan is optimized for a single-session one-shot build under a
> hard time deadline (demo tomorrow). It is organized by component in build-tier order
> (T0 core → T1 Slack → T2 lemlist), with complete file contents and smoke/dry-run
> verification instead of per-step TDD. Each file is self-contained; create them top to bottom.

**Goal:** A runnable signal→score→draft→write-back loop that, per retail/CPG account, collects
real Exa intent signals in parallel, scores ICP fit + drafts personalized outreach with an
anti-hallucination guard, then writes to HubSpot (always), and for Tier 1 alerts Slack and
queues a paused lemlist draft for rep approval.

**Architecture:** Per-account pipeline. Parallel Exa collectors → deterministic signal
validation → single guarded Claude tool_use call (synthesize + score + draft) → deterministic
post-guard → write-back fan-out. Write-back channels are isolated (one failing channel never
aborts the run). A `--dry-run` flag stubs all three write-backs for safe iteration and as a
fallback during the live demo.

**Tech Stack:** Python 3.11+, exa-py, anthropic (forced tool_use), requests, pydantic, jinja2,
python-dotenv.

---

## File structure

```
duvo-signal-loop/
├── .env.example
├── requirements.txt
├── README.md
├── companies.csv
├── config.py
├── models.py
├── signal_collector.py        # T0 — parallel Exa
├── scorer.py                  # T0 — guarded Claude tool_use
├── writeback/
│   ├── __init__.py
│   ├── hubspot.py             # T0
│   ├── slack.py               # T1
│   └── lemlist.py             # T2
├── reporter.py                # T0
├── templates/report.html      # T0
├── main.py                    # T0 (extended in T1/T2)
└── output/                    # generated
```

---

## Pre-build setup (do once, ~10 min, manual)

1. **HubSpot:** create a free developer test account → Settings → Integrations → Private Apps →
   create app with scopes `crm.objects.companies.read/write`, `crm.objects.contacts.read/write`,
   `crm.objects.notes.read/write` (notes write is covered by the objects scopes), and
   `crm.schemas.companies.read/write` (to create the custom property). Copy the token.
2. **Slack:** create an Incoming Webhook for a test channel (#sales). Copy the webhook URL.
3. **lemlist:** create ONE campaign, leave it **paused/draft**, copy its campaign ID (from the
   URL or `GET https://api.lemlist.com/api/campaigns` with basic auth `:<API_KEY>`). Copy the
   API key (Settings → Integrations → API).
4. **Exa + Anthropic:** copy both API keys.

---

## Task 0: Project scaffold

**Files:** `requirements.txt`, `.env.example`, `companies.csv`, `writeback/__init__.py`

`requirements.txt`:
```
exa-py>=1.0.0
anthropic>=0.40.0
pydantic>=2.0
jinja2>=3.1
python-dotenv>=1.0
requests>=2.31
```

`.env.example`:
```
EXA_API_KEY=
ANTHROPIC_API_KEY=
HUBSPOT_TOKEN=
SLACK_WEBHOOK_URL=
LEMLIST_API_KEY=
LEMLIST_CAMPAIGN_ID=
TEST_EMAIL=jakubkubala3@gmail.com
```

`writeback/__init__.py`: (empty file)

`companies.csv` — 8 real EU retail/CPG accounts with plausible public triggers + 2 deliberately
weak/small ones to make the guard fire on camera:
```csv
name,domain,country,description
Rohlik Group,rohlik.cz,CZ,Online grocery delivery scaling across CEE and DACH
Notino,notino.cz,CZ,Large online beauty/cosmetics retailer expanding across Europe
Kaufland Czech Republic,kaufland.cz,CZ,Hypermarket chain part of Schwarz Group
Albert (Ahold Delhaize CZ),albert.cz,CZ,Supermarket chain owned by Ahold Delhaize
Tesco Central Europe,itesco.cz,CZ,Large grocery retailer across CEE
Dr. Max Group,drmax.cz,CZ,Largest CEE pharmacy retail chain
Pilulka Lekarny,pilulka.cz,CZ,Online and offline pharmacy retailer
Mall Group / Allegro CZ,mall.cz,CZ,E-commerce marketplace operating in CEE
Tiny Local Bakery,tinylocalbakery-demo.cz,CZ,Small single-store bakery (deliberate weak signal)
Garage Startup XYZ,garage-xyz-demo.io,SK,Two-person pre-seed startup (deliberate weak signal)
```

**Verify:**
```bash
cd duvo-signal-loop
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in real keys
```

**Commit:**
```bash
git add requirements.txt .env.example companies.csv writeback/__init__.py
git commit -m "scaffold: deps, env template, target list"
```

---

## Task 1: config.py

**Files:** Create `config.py`

```python
"""Load API credentials from .env. Exa + Anthropic are always required;
write-back keys are validated lazily by their own modules so --dry-run works
without them."""
import os
from dotenv import load_dotenv

load_dotenv()

EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
LEMLIST_API_KEY = os.environ.get("LEMLIST_API_KEY", "")
LEMLIST_CAMPAIGN_ID = os.environ.get("LEMLIST_CAMPAIGN_ID", "")
TEST_EMAIL = os.environ.get("TEST_EMAIL", "jakubkubala3@gmail.com")

CLAUDE_MODEL = "claude-sonnet-4-6"  # fast + strong for the live demo; swap to opus if desired


def require(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required env var: {name}. Add it to .env")
    return value
```

**Verify:**
```bash
python -c "import config; print('exa set:', bool(config.EXA_API_KEY))"
```
Expected: `exa set: True`

**Commit:**
```bash
git add config.py && git commit -m "feat: config loader"
```

---

## Task 2: models.py

**Files:** Create `models.py`

```python
"""Pydantic models — the contract shared across the pipeline."""
from __future__ import annotations
from pydantic import BaseModel


class Company(BaseModel):
    name: str
    domain: str
    country: str = ""
    description: str = ""


class Signal(BaseModel):
    signal_type: str          # erp_migration | hiring | ma_leadership | pain
    title: str
    summary: str
    source_url: str
    published_date: str | None = None
    relevance: str = ""


class OutreachDraft(BaseModel):
    persona: str
    subject: str
    first_line: str
    body: str


class ICPScore(BaseModel):
    company_name: str
    domain: str
    score: int                # 1-10
    tier: str                 # "Tier 1" | "Tier 2" | "Tier 3"
    confidence: str           # "high" | "medium" | "low"
    why_fit: list[str]
    why_not: list[str]
    recommended_persona: str
    recommended_angle: str
    reasoning: str
    needs_human_research: bool
    outreach: OutreachDraft


class RunResult(BaseModel):
    score: ICPScore
    signals: list[Signal]
    hubspot_status: str = "skipped"
    slack_status: str = "skipped"
    lemlist_status: str = "skipped"
```

**Verify:**
```bash
python -c "from models import ICPScore, OutreachDraft; print('models ok')"
```
Expected: `models ok`

**Commit:**
```bash
git add models.py && git commit -m "feat: pydantic data models"
```

---

## Task 3: signal_collector.py (T0 — parallel Exa)

**Files:** Create `signal_collector.py`

Defensive: tolerant of empty results per query, never crashes the account. Reads result
attributes via `getattr` so SDK shape differences don't break the run.

```python
"""Collect real intent signals for one company via 4 parallel Exa searches."""
from concurrent.futures import ThreadPoolExecutor
from exa_py import Exa

from config import EXA_API_KEY, require
from models import Company, Signal

_exa = Exa(api_key=require("EXA_API_KEY", EXA_API_KEY))

# (signal_type, query_template, summary_query, start_published_date)
QUERIES = [
    ("erp_migration",
     "{name} SAP Oracle ERP migration S/4HANA implementation supply chain or finance software",
     "Is this company migrating ERP or rolling out new supply chain / finance software?",
     "2024-01-01"),
    ("hiring",
     "{name} hiring supply chain procurement operations finance director or manager",
     "Recent hiring in supply chain, procurement, operations, or finance.",
     "2025-01-01"),
    ("ma_leadership",
     "{name} acquisition merger new CFO CEO COO supply chain leadership change",
     "M&A activity or a leadership change relevant to procurement or finance.",
     "2025-01-01"),
    ("pain",
     "{name} supply chain invoice matching reconciliation manual process inventory inefficiency",
     "Operational pain: manual reconciliation, invoice/PO matching, supplier portal chaos.",
     None),
]


def _one_query(company: Company, spec) -> list[Signal]:
    signal_type, qtmpl, summary_q, start = spec
    query = qtmpl.format(name=company.name)
    kwargs = {"num_results": 3, "summary": {"query": summary_q}}
    if start:
        kwargs["start_published_date"] = start
    try:
        res = _exa.search_and_contents(query, **kwargs)
    except Exception as exc:  # network / rate / bad query — degrade, don't crash
        print(f"      [exa] {signal_type} failed: {exc}")
        return []
    out: list[Signal] = []
    for r in getattr(res, "results", []):
        summary = (getattr(r, "summary", None) or "").strip()
        out.append(Signal(
            signal_type=signal_type,
            title=(getattr(r, "title", None) or "(no title)").strip(),
            summary=summary[:600],
            source_url=getattr(r, "url", "") or "",
            published_date=getattr(r, "published_date", None),
            relevance=summary_q,
        ))
    return out


def collect_signals(company: Company) -> list[Signal]:
    """Run the 4 Exa queries concurrently and flatten the results."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        groups = list(ex.map(lambda s: _one_query(company, s), QUERIES))
    return [sig for group in groups for sig in group]
```

**Verify (real Exa call):**
```bash
python -c "
from models import Company
from signal_collector import collect_signals
sigs = collect_signals(Company(name='Rohlik Group', domain='rohlik.cz', country='CZ'))
print('signals:', len(sigs))
[print('-', s.signal_type, '|', s.published_date, '|', s.title[:60]) for s in sigs[:6]]
"
```
Expected: prints several signals (count may vary). No traceback.

**Commit:**
```bash
git add signal_collector.py && git commit -m "feat: parallel Exa signal collector"
```

---

## Task 4: scorer.py (T0 — guarded Claude tool_use)

**Files:** Create `scorer.py`

The "validating synthesizer with guards" is realized as: (a) deterministic signal validation in
the collector, (b) one forced-tool_use Claude call that synthesizes + scores + drafts with
explicit honesty instructions, and (c) `apply_guards()` — a deterministic post-guard that caps
hallucination. `apply_guards` is a separate, demonstrable function so it can be shown firing
live.

```python
"""Score ICP fit and draft outreach for one company, with anti-hallucination guards."""
import json
from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, require
from models import Company, Signal, ICPScore, OutreachDraft

_client = Anthropic(api_key=require("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY))

ICP_DEFINITION = """Duvo AI closes operational back-office work end-to-end for enterprise
retail and CPG. Its agents log into SAP/ERP, supplier portals, email and spreadsheets and write
back evidence Finance accepts. Ideal customer profile:
- Industry: retail, grocery, CPG, e-commerce, pharmacy retail.
- Size: 100M EUR+ revenue OR 500+ employees.
- Tech: runs SAP / Oracle / similar ERP; manual reconciliation, PO/invoice matching in Excel,
  supplier-portal chaos.
- Geography: Central & Eastern Europe preferred, Western Europe fine.
- Trigger events: ERP migration, M&A, new CFO / Supply Chain Director, rapid expansion.

Scoring (1-10):
 9-10 perfect fit: retail/CPG, has ERP, clear operational pain, trigger event present.
 7-8 strong: right industry, likely has ERP, some pain signals.
 5-6 moderate: adjacent industry or missing a key trigger.
 3-4 weak: wrong industry or too small.
 1-2 not a fit.
"""

TOOL = {
    "name": "record_icp_assessment",
    "description": "Record the ICP fit assessment and a drafted first-touch outreach.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "description": "ICP fit 1-10"},
            "tier": {"type": "string", "enum": ["Tier 1", "Tier 2", "Tier 3"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "why_fit": {"type": "array", "items": {"type": "string"}},
            "why_not": {"type": "array", "items": {"type": "string"}},
            "recommended_persona": {"type": "string"},
            "recommended_angle": {"type": "string"},
            "reasoning": {"type": "string"},
            "needs_human_research": {
                "type": "boolean",
                "description": "True if signals are too thin to trust the score.",
            },
            "outreach": {
                "type": "object",
                "properties": {
                    "persona": {"type": "string"},
                    "subject": {"type": "string"},
                    "first_line": {"type": "string",
                                   "description": "Personalized opener grounded in a real signal."},
                    "body": {"type": "string"},
                },
                "required": ["persona", "subject", "first_line", "body"],
            },
        },
        "required": ["score", "tier", "confidence", "why_fit", "why_not",
                     "recommended_persona", "recommended_angle", "reasoning",
                     "needs_human_research", "outreach"],
    },
}


def _prompt(company: Company, signals: list[Signal]) -> str:
    signals_json = json.dumps([s.model_dump() for s in signals], ensure_ascii=False, indent=2)
    return f"""{ICP_DEFINITION}

Company: {company.name} ({company.domain}, {company.country})
Context: {company.description}

Signals found via web search (each has a source_url and published_date — null date = unverified):
{signals_json}

Assess this company's ICP fit for Duvo and draft a first-touch outreach.

CRITICAL HONESTY RULES:
- Ground every claim ONLY in the signals above. Do NOT invent ERP systems, deals, or pain you
  cannot see in a signal.
- If the signals are thin, generic, or undated, say so: set confidence="low" and
  needs_human_research=true, and keep the score conservative.
- The outreach first_line MUST reference a real signal (or be generic if none exists) — never
  fabricate a specific event.
Return your answer by calling record_icp_assessment."""


def synthesize_and_score(company: Company, signals: list[Signal]) -> ICPScore:
    resp = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "record_icp_assessment"},
        messages=[{"role": "user", "content": _prompt(company, signals)}],
    )
    data = next(b.input for b in resp.content if b.type == "tool_use")
    outreach = OutreachDraft(**data.pop("outreach"))
    score = ICPScore(company_name=company.name, domain=company.domain,
                     outreach=outreach, **data)
    return apply_guards(score, signals)


def apply_guards(score: ICPScore, signals: list[Signal]) -> ICPScore:
    """Deterministic post-guard: thin/undated evidence cannot yield a confident high score."""
    dated = [s for s in signals if s.published_date]
    if not dated:
        score.confidence = "low"
        score.needs_human_research = True
    if score.confidence == "low" and score.score >= 7:
        score.score = 6  # cap hallucinated enthusiasm
    if score.score >= 8 and not score.needs_human_research:
        score.tier = "Tier 1"
    elif score.score >= 5:
        score.tier = "Tier 2"
    else:
        score.tier = "Tier 3"
    if score.needs_human_research and score.tier == "Tier 1":
        score.tier = "Tier 2"  # never auto-promote unverified accounts
    return score
```

**Verify (real call against a strong and a weak account):**
```bash
python -c "
from models import Company
from signal_collector import collect_signals
from scorer import synthesize_and_score
for c in [Company(name='Rohlik Group', domain='rohlik.cz', country='CZ'),
          Company(name='Tiny Local Bakery', domain='tinylocalbakery-demo.cz', country='CZ',
                  description='Small single-store bakery')]:
    s = synthesize_and_score(c, collect_signals(c))
    print(c.name, '->', s.score, s.tier, s.confidence, 'human?', s.needs_human_research)
    print('   first_line:', s.outreach.first_line[:90])
"
```
Expected: Rohlik scores high (Tier 1/2, confidence not low); the bakery scores low, confidence
low, `needs_human_research=True`, Tier 2/3 — **the guard firing on camera.**

**Commit:**
```bash
git add scorer.py && git commit -m "feat: guarded Claude scorer + outreach drafting"
```

---

## Task 5: writeback/hubspot.py (T0)

**Files:** Create `writeback/hubspot.py`

```python
"""Write the assessment back into HubSpot: ensure custom property, upsert company, attach a
note with the evidence + recommended angle (labeled AI-suggested)."""
import time
import requests

from config import HUBSPOT_TOKEN, require
from models import ICPScore

_BASE = "https://api.hubapi.com"
_NOTE_TO_COMPANY = 190  # HubSpot default association type id


def _headers() -> dict:
    return {"Authorization": f"Bearer {require('HUBSPOT_TOKEN', HUBSPOT_TOKEN)}",
            "Content-Type": "application/json"}


def ensure_icp_property() -> None:
    url = f"{_BASE}/crm/v3/properties/companies/icp_score"
    if requests.get(url, headers=_headers()).status_code == 200:
        return
    requests.post(f"{_BASE}/crm/v3/properties/companies", headers=_headers(), json={
        "name": "icp_score", "label": "ICP Score", "type": "number",
        "fieldType": "number", "groupName": "companyinformation",
    })


def _upsert_company(score: ICPScore) -> str:
    search = {"filterGroups": [{"filters": [
        {"propertyName": "domain", "operator": "EQ", "value": score.domain}]}],
        "properties": ["domain", "name"]}
    r = requests.post(f"{_BASE}/crm/v3/objects/companies/search",
                      headers=_headers(), json=search)
    results = r.json().get("results", [])
    props = {"name": score.company_name, "domain": score.domain,
             "icp_score": score.score}
    if results:
        cid = results[0]["id"]
        requests.patch(f"{_BASE}/crm/v3/objects/companies/{cid}",
                       headers=_headers(), json={"properties": props})
    else:
        cr = requests.post(f"{_BASE}/crm/v3/objects/companies",
                           headers=_headers(), json={"properties": props})
        cid = cr.json()["id"]
    return cid


def _note_body(score: ICPScore) -> str:
    lines = [
        "AI-SUGGESTED — review before outreach.",
        f"ICP score: {score.score}/10 ({score.tier}), confidence {score.confidence}.",
        f"Persona: {score.recommended_persona}",
        f"Angle: {score.recommended_angle}",
        "", "Why fit: " + "; ".join(score.why_fit),
        "Why not: " + "; ".join(score.why_not),
        "", "Reasoning: " + score.reasoning,
        "", "Drafted opener: " + score.outreach.first_line,
    ]
    if score.needs_human_research:
        lines.insert(1, ">> FLAGGED: signals too thin — human research needed before contact.")
    return "<br>".join(lines)


def _create_note(score: ICPScore, company_id: str) -> None:
    payload = {
        "properties": {"hs_note_body": _note_body(score),
                       "hs_timestamp": int(time.time() * 1000)},
        "associations": [{"to": {"id": company_id}, "types": [
            {"associationCategory": "HUBSPOT_DEFINED",
             "associationTypeId": _NOTE_TO_COMPANY}]}],
    }
    requests.post(f"{_BASE}/crm/v3/objects/notes", headers=_headers(), json=payload)


def upsert_account(score: ICPScore) -> str:
    ensure_icp_property()
    cid = _upsert_company(score)
    _create_note(score, cid)
    return f"company {cid} (icp_score={score.score}) + evidence note"
```

**Verify (writes to your sandbox, then read back in the UI):**
```bash
python -c "
from models import Company
from signal_collector import collect_signals
from scorer import synthesize_and_score
from writeback import hubspot
c = Company(name='Rohlik Group', domain='rohlik.cz', country='CZ')
print(hubspot.upsert_account(synthesize_and_score(c, collect_signals(c))))
"
```
Expected: prints `company <id> (icp_score=...) + evidence note`. Confirm in HubSpot UI:
the company has an ICP Score property and a note.

**Commit:**
```bash
git add writeback/hubspot.py && git commit -m "feat: HubSpot write-back (property + note)"
```

---

## Task 6: reporter.py + templates/report.html (T0)

**Files:** Create `reporter.py`, `templates/report.html`

`reporter.py`:
```python
"""Render a local HTML audit log of the run — for the Loom, NOT the deliverable."""
import os
from jinja2 import Environment, FileSystemLoader, select_autoescape

from models import RunResult

_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
    autoescape=select_autoescape(["html"]),
)


def generate_report(results: list[RunResult], path: str = "output/run-report.html") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered = sorted(results, key=lambda r: r.score.score, reverse=True)
    html = _env.get_template("report.html").render(results=ordered)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path
```

`templates/report.html`:
```html
<!doctype html><html><head><meta charset="utf-8"><title>duvo-signal-loop run</title>
<style>
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:24px 32px;background:#161922;border-bottom:1px solid #262b36}
 h1{margin:0;font-size:20px} .sub{color:#8a93a2;font-size:13px;margin-top:4px}
 .card{margin:16px 32px;border:1px solid #262b36;border-radius:10px;overflow:hidden;background:#161922}
 .top{display:flex;align-items:center;gap:14px;padding:14px 18px;border-bottom:1px solid #262b36}
 .badge{font-weight:700;border-radius:6px;padding:4px 10px;color:#0f1115}
 .t1{background:#34d399}.t2{background:#fbbf24}.t3{background:#f87171}
 .name{font-size:16px;font-weight:600} .muted{color:#8a93a2}
 .flag{margin-left:auto;background:#7c3aed;color:#fff;border-radius:6px;padding:4px 10px;font-size:12px}
 .body{padding:14px 18px;display:grid;grid-template-columns:1fr 1fr;gap:18px}
 .lab{color:#8a93a2;text-transform:uppercase;font-size:11px;letter-spacing:.04em;margin:10px 0 4px}
 ul{margin:4px 0;padding-left:18px} a{color:#60a5fa}
 .draft{grid-column:1/3;background:#0f1115;border:1px solid #262b36;border-radius:8px;padding:12px}
 .status{grid-column:1/3;font-size:12px;color:#8a93a2;border-top:1px dashed #262b36;padding-top:8px}
 code{background:#0f1115;padding:1px 5px;border-radius:4px}
</style></head><body>
<header><h1>duvo-signal-loop — run report</h1>
<div class="sub">{{ results|length }} accounts scored · sorted by ICP score · audit log only</div></header>
{% for r in results %}{% set s = r.score %}
<div class="card">
 <div class="top">
  <span class="badge {% if s.tier=='Tier 1' %}t1{% elif s.tier=='Tier 2' %}t2{% else %}t3{% endif %}">{{ s.score }}/10</span>
  <div><div class="name">{{ s.company_name }} <span class="muted">· {{ s.domain }}</span></div>
   <div class="muted">{{ s.tier }} · confidence {{ s.confidence }} · {{ s.recommended_persona }}</div></div>
  {% if s.needs_human_research %}<span class="flag">human research needed</span>{% endif %}
 </div>
 <div class="body">
  <div><div class="lab">Why fit</div><ul>{% for w in s.why_fit %}<li>{{ w }}</li>{% endfor %}</ul>
       <div class="lab">Angle</div><div>{{ s.recommended_angle }}</div></div>
  <div><div class="lab">Signals ({{ r.signals|length }})</div><ul>
       {% for g in r.signals %}<li><a href="{{ g.source_url }}" target="_blank">{{ g.signal_type }}</a>
       <span class="muted">{{ g.published_date or 'no date' }}</span> — {{ g.title }}</li>{% endfor %}</ul></div>
  <div class="draft"><div class="lab">Drafted outreach (paused — rep approves)</div>
   <div><b>Subject:</b> {{ s.outreach.subject }}</div>
   <div style="margin-top:6px">{{ s.outreach.first_line }}</div>
   <div style="margin-top:6px" class="muted">{{ s.outreach.body }}</div></div>
  <div class="status">HubSpot: <code>{{ r.hubspot_status }}</code> ·
   Slack: <code>{{ r.slack_status }}</code> · lemlist: <code>{{ r.lemlist_status }}</code></div>
 </div>
</div>{% endfor %}
</body></html>
```

**Verify (after main exists, Task 7).**

**Commit:**
```bash
git add reporter.py templates/report.html
git commit -m "feat: HTML run report (audit log)"
```

---

## Task 7: main.py (T0 — orchestrator with --dry-run)

**Files:** Create `main.py`

```python
"""Orchestrate the loop: load accounts → per-account signals+score → write-back → report."""
import argparse
import csv
import time

from config import TEST_EMAIL
from models import Company, RunResult
from signal_collector import collect_signals
from scorer import synthesize_and_score
from reporter import generate_report
from writeback import hubspot


def load_companies(path: str = "companies.csv") -> list[Company]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [Company(**row) for row in csv.DictReader(fh)]


def _writeback(rr: RunResult, dry_run: bool, test_email: str) -> None:
    s = rr.score
    if dry_run:
        rr.hubspot_status = f"[dry-run] upsert company + note (icp_score={s.score})"
        if s.tier == "Tier 1" and not s.needs_human_research:
            rr.slack_status = "[dry-run] alert #sales"
            rr.lemlist_status = "[dry-run] queue paused lemlist draft"
        return
    try:
        rr.hubspot_status = hubspot.upsert_account(s)
    except Exception as exc:
        rr.hubspot_status = f"ERROR: {exc}"
    # Slack + lemlist wired in T1/T2 below.


def run(dry_run: bool, test_email: str) -> None:
    companies = load_companies()
    print(f"Scoring {len(companies)} accounts (dry_run={dry_run})...")
    results: list[RunResult] = []
    for c in companies:
        print(f"  -> {c.name}")
        signals = collect_signals(c)
        score = synthesize_and_score(c, signals)
        print(f"     {score.score}/10 {score.tier} conf={score.confidence} "
              f"human={score.needs_human_research} ({len(signals)} signals)")
        rr = RunResult(score=score, signals=signals)
        _writeback(rr, dry_run, test_email)
        results.append(rr)
        time.sleep(0.5)  # be kind to Exa rate limits
    path = generate_report(results)
    print(f"\nDone. Open {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="run Exa+scoring but stub all write-backs")
    ap.add_argument("--test-email", default=TEST_EMAIL,
                    help="email used as the lemlist test lead")
    args = ap.parse_args()
    run(dry_run=args.dry_run, test_email=args.test_email)
```

**Verify (full dry run — no external writes):**
```bash
python main.py --dry-run
open output/run-report.html   # macOS
```
Expected: every account prints a score line; the two demo accounts show `human=True`; the report
opens with Tier-1 accounts on top and the guard-flagged accounts marked.

**Then a real T0 run (HubSpot writes only):**
```bash
python main.py
```
Expected: HubSpot status shows real company IDs; check the UI.

**Commit:**
```bash
git add main.py && git commit -m "feat: orchestrator with --dry-run"
```

---

## Task 8: writeback/slack.py (T1)

**Files:** Create `writeback/slack.py`; modify `main.py` `_writeback`.

`writeback/slack.py`:
```python
"""Post a Tier-1 alert to #sales via incoming webhook."""
import requests

from config import SLACK_WEBHOOK_URL, require
from models import ICPScore


def alert_tier1(score: ICPScore) -> str:
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
         "text": f"\U0001F7E2 Tier 1: {score.company_name} ({score.score}/10)"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Persona*\n{score.recommended_persona}"},
            {"type": "mrkdwn", "text": f"*Confidence*\n{score.confidence}"},
            {"type": "mrkdwn", "text": f"*Angle*\n{score.recommended_angle}"},
            {"type": "mrkdwn", "text": f"*Domain*\n{score.domain}"}]},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Drafted opener (paused in lemlist — approve to send):*\n>{score.outreach.first_line}"}},
    ]
    r = requests.post(require("SLACK_WEBHOOK_URL", SLACK_WEBHOOK_URL),
                      json={"blocks": blocks, "text": f"Tier 1: {score.company_name}"})
    r.raise_for_status()
    return "alert posted to #sales"
```

Modify `main.py` — replace the comment line `# Slack + lemlist wired in T1/T2 below.` with:
```python
    if s.tier == "Tier 1" and not s.needs_human_research:
        from writeback import slack
        try:
            rr.slack_status = slack.alert_tier1(s)
        except Exception as exc:
            rr.slack_status = f"ERROR: {exc}"
```

**Verify:**
```bash
python -c "
from models import ICPScore, OutreachDraft
from writeback import slack
s = ICPScore(company_name='Rohlik Group', domain='rohlik.cz', score=9, tier='Tier 1',
  confidence='high', why_fit=['retail','ERP'], why_not=[], recommended_persona='Supply Chain Director',
  recommended_angle='ERP migration reconciliation pain', reasoning='strong', needs_human_research=False,
  outreach=OutreachDraft(persona='Supply Chain Director', subject='x', first_line='Saw your CEE expansion', body='y'))
print(slack.alert_tier1(s))
"
```
Expected: message appears in the Slack channel; prints `alert posted to #sales`.

**Commit:**
```bash
git add writeback/slack.py main.py && git commit -m "feat: Slack Tier-1 alert"
```

---

## Task 9: writeback/lemlist.py (T2 — riskiest, last)

**Files:** Create `writeback/lemlist.py`; modify `main.py` `_writeback`.

API: `POST https://api.lemlist.com/api/campaigns/{campaignId}/leads` with basic auth
(`username=""`, `password=<API_KEY>`). The campaign must already exist and be **paused**, so the
lead is queued as a draft and never auto-sends.

`writeback/lemlist.py`:
```python
"""Queue a Tier-1 lead into a PAUSED lemlist campaign as a draft for rep approval."""
import requests

from config import LEMLIST_API_KEY, LEMLIST_CAMPAIGN_ID, require
from models import ICPScore

_BASE = "https://api.lemlist.com/api"


def queue_draft(score: ICPScore, test_email: str) -> str:
    campaign = require("LEMLIST_CAMPAIGN_ID", LEMLIST_CAMPAIGN_ID)
    url = f"{_BASE}/campaigns/{campaign}/leads"
    payload = {
        "email": test_email,                       # demo: candidate's own email as test lead
        "companyName": score.company_name,
        "companyDomain": score.domain,
        "jobTitle": score.recommended_persona,
        "icpScore": str(score.score),              # custom variable usable in lemlist templates
        "icebreaker": score.outreach.first_line,   # custom variable for the opener
    }
    r = requests.post(url, auth=("", require("LEMLIST_API_KEY", LEMLIST_API_KEY)),
                      json=payload, params={"deduplicate": "true"})
    r.raise_for_status()
    return f"lead queued in paused campaign {campaign} (awaiting rep approval)"
```

Modify `main.py` — inside the `if s.tier == "Tier 1" and not s.needs_human_research:` block,
after the Slack try/except, add:
```python
        from writeback import lemlist
        try:
            rr.lemlist_status = lemlist.queue_draft(s, test_email)
        except Exception as exc:
            rr.lemlist_status = f"ERROR: {exc}"
```

**Verify:**
```bash
python -c "
from models import ICPScore, OutreachDraft
from writeback import lemlist
s = ICPScore(company_name='Rohlik Group', domain='rohlik.cz', score=9, tier='Tier 1',
  confidence='high', why_fit=['x'], why_not=[], recommended_persona='Supply Chain Director',
  recommended_angle='x', reasoning='x', needs_human_research=False,
  outreach=OutreachDraft(persona='Supply Chain Director', subject='x', first_line='Saw your CEE expansion', body='y'))
print(lemlist.queue_draft(s, 'jakubkubala3@gmail.com'))
"
```
Expected: prints `lead queued in paused campaign ...`. Confirm the lead appears in the campaign
in lemlist and the campaign is paused. If the API errors, the loop still completes (status shows
ERROR); fall back to `--dry-run` + a screenshot of a manually added lead, and say so in the note.

**Commit:**
```bash
git add writeback/lemlist.py main.py && git commit -m "feat: lemlist paused draft queue"
```

---

## Task 10: Full run + README

**Files:** Create `README.md`

Run the whole loop for real:
```bash
python main.py
open output/run-report.html
```
Expected: Tier-1 accounts → HubSpot note + Slack alert + lemlist draft; weak accounts →
HubSpot note flagged for human research, no Slack/lemlist.

`README.md` contents:
```markdown
# duvo-signal-loop

Signal → score → draft → write-back loop for Duvo's GTM stack.
Per retail/CPG account: parallel Exa intent signals → guarded Claude scoring + drafted
outreach → HubSpot (always) + Slack alert + paused lemlist draft (Tier 1).

## Setup
1. `python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. `cp .env.example .env` and fill keys (Exa, Anthropic, HubSpot private-app token,
   Slack webhook, lemlist API key + a **paused** campaign id).

## Run
- `python main.py --dry-run`  — Exa + scoring only, no external writes (safe demo fallback).
- `python main.py`            — full loop with write-backs.
- Output audit log: `output/run-report.html`.

## Where a human stays in the loop
- The system never sends. lemlist campaigns stay **paused**; the rep approves and sends.
- Thin/undated signals → `apply_guards()` caps the score, flags `needs_human_research`, and
  skips Slack/lemlist. Demonstrated by the two intentionally weak accounts in `companies.csv`.
- HubSpot notes are labeled "AI-suggested — review before outreach."

## Where it breaks
- Exa noise/staleness on large brands (guard mitigates, recall limited).
- No real person-level email — persona is recommended; the demo uses a test email as the lead.
- One-shot script; production = weekly cron + score diff + alert only on change.
- No dedup vs. existing HubSpot pipeline.

## What I'd build next (one week)
Net-new account discovery via Exa at the front · Gong call-outcome → HubSpot write-back ·
lemlist reply handling branching on intent · person-level enrichment (Apollo) for real emails.
```

**Commit:**
```bash
git add README.md && git commit -m "docs: README"
```

---

## Self-review (spec coverage)

- Parallel Exa collectors → Task 3. ✓
- Validating synth + anti-hallucination guard → Task 4 (`apply_guards`, honesty prompt). ✓
- Score + tier + confidence + persona + angle + per-persona outreach draft → Task 4 tool schema. ✓
- HubSpot write-back (custom property + evidence note) → Task 5. ✓
- Slack Tier-1 alert → Task 8. ✓
- lemlist paused draft → Task 9. ✓
- Human-in-the-loop (never sends; guard skips weak accounts; AI-suggested label) → Tasks 4,5,8,9. ✓
- `--dry-run` safe mode → Task 7. ✓
- Layered build T0→T1→T2 → Tasks 3-7 (T0), 8 (T1), 9 (T2). ✓
- HTML audit log (not the deliverable) → Task 6. ✓
- Target list incl. 2 weak accounts → Task 0. ✓
- README with why / where it breaks / next → Task 10. ✓

Type consistency: `ICPScore`/`OutreachDraft`/`Signal`/`RunResult` field names are identical
across Tasks 2, 4, 5, 6, 8, 9. Write-back functions return strings assigned to
`RunResult.*_status`. ✓
```
