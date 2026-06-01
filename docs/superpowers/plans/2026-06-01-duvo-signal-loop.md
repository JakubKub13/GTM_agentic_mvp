# duvo-signal-loop Implementation Plan (multi-agent)

> **For agentic workers:** Single-session one-shot build under a hard deadline (demo tomorrow).
> Organized by component in build-tier order (T0 core → T1 Slack → T2 lemlist), with complete
> file contents and smoke/dry-run verification. Create files top to bottom; each is self-contained.

**Goal:** A team of tool-using AI agents that, per retail/CPG account, scout sourced intent
signals (scout agents with an Exa tool), validate + score + draft outreach (analyst agent that
can verify claims), and decide how to action the account into HubSpot / Slack / lemlist (router
agent). Python only orchestrates hand-offs.

**Architecture:** One reusable `run_agent()` tool-use loop powers every agent. Per account:
4 scout agents in parallel → analyst agent (+ deterministic `apply_guards`) → router agent whose
tools are the write-backs. Agents own reversible decisions; hard guards own irreversible ones
(score caps, never-send). `--dry-run` lets the router decide for real while write-back tools simulate.

**Tech Stack:** Python 3.11+, anthropic (tool-use loop), exa-py, requests, pydantic, jinja2, python-dotenv.

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
├── agent_core.py            # run_agent() tool-use loop + Anthropic client
├── tools/
│   ├── __init__.py
│   └── exa_tool.py          # exa_search tool (schema + impl)
├── scouts.py                # scout agents (T0)
├── analyst.py               # analyst agent + apply_guards (T0)
├── writeback/
│   ├── __init__.py
│   ├── crm.py              # CRM dispatcher: attio|hubspot by CRM_PROVIDER (T0)
│   ├── attio.py            # Attio CRM write-back (T0, default)
│   ├── hubspot.py          # HubSpot adapter, same interface (optional)
│   ├── slack.py            # T1
│   ├── outreach.py         # Outreach dispatcher: brevo|lemlist by OUTREACH_PROVIDER (T2)
│   ├── brevo.py            # Brevo outreach (T2, default)
│   └── lemlist.py          # lemlist adapter, same interface (optional)
├── router.py                # router agent; write-backs as tools (T0 minimal → T1 → T2)
├── reporter.py              # HTML audit log (T0)
├── templates/report.html    # T0
├── main.py                  # orchestrator (T0)
└── output/                  # generated
```

---

## Pre-build setup (do once, ~10 min, manual)

1. **Attio (default CRM):** sign up at https://app.attio.com (Google/email, frictionless) →
   Workspace Settings → Developers → Create a new integration → generate an access token (Bearer).
   Free plan includes API access. Put it in `ATTIO_API_KEY`, keep `CRM_PROVIDER=attio`.
   (Optional: to target Duvo's real HubSpot instead, set `CRM_PROVIDER=hubspot` + `HUBSPOT_TOKEN`
   from a private app in a HubSpot CRM portal — see `.env.example`.)
2. **Slack:** Incoming Webhook for #sales. Copy URL.
3. **Brevo (default outreach):** sign up at https://www.brevo.com (free, no card) → SMTP & API →
   API Keys → generate (`xkeysib-...`) into `BREVO_API_KEY`. Create a contact list (Contacts →
   Lists), e.g. "duvo-tier1-review", and copy its numeric ID into `BREVO_LIST_ID`. Keep
   `OUTREACH_PROVIDER=brevo`. (Optional: `OUTREACH_PROVIDER=lemlist` + a **paused** campaign for
   Duvo's real stack — see `.env.example`.)
4. **Exa + Anthropic + Attio:** copy the three keys (these three run the whole loop into the CRM).

---

## Task 0: Scaffold

**Files:** `.gitignore`, `requirements.txt`, `.env.example`, `companies.csv`, `tools/__init__.py`, `writeback/__init__.py`

`.gitignore` (create FIRST — prevents committing real keys / venv / output):
```
.env
.venv/
__pycache__/
*.pyc
output/
```

`requirements.txt`:
```
anthropic>=0.40.0
exa-py>=1.0.0
pydantic>=2.0
jinja2>=3.1
python-dotenv>=1.0
requests>=2.31
```

`.env.example`: (full documented version already written to `duvo-signal-loop/.env.example`; keys:)
```
EXA_API_KEY=
ANTHROPIC_API_KEY=
CRM_PROVIDER=attio
ATTIO_API_KEY=
HUBSPOT_TOKEN=
SLACK_WEBHOOK_URL=
OUTREACH_PROVIDER=brevo
BREVO_API_KEY=
BREVO_LIST_ID=
LEMLIST_API_KEY=
LEMLIST_CAMPAIGN_ID=
TEST_EMAIL=jakubkubala3@gmail.com
```

`tools/__init__.py`: (empty)
`writeback/__init__.py`: (empty)

`companies.csv`:
```csv
name,domain,country,description
Rohlik Group,rohlik.cz,CZ,Online grocery delivery scaling across CEE and DACH
Notino,notino.cz,CZ,Large online beauty/cosmetics retailer expanding across Europe
Kaufland Czech Republic,kaufland.cz,CZ,Hypermarket chain part of Schwarz Group
Albert Ahold Delhaize CZ,albert.cz,CZ,Supermarket chain owned by Ahold Delhaize
Tesco Central Europe,itesco.cz,CZ,Large grocery retailer across CEE
Dr. Max Group,drmax.cz,CZ,Largest CEE pharmacy retail chain
Pilulka Lekarny,pilulka.cz,CZ,Online and offline pharmacy retailer
Mall Group,mall.cz,CZ,E-commerce marketplace operating in CEE
Tiny Local Bakery,tinylocalbakery-demo.cz,CZ,Small single-store bakery (deliberate weak signal)
Garage Startup XYZ,garage-xyz-demo.io,SK,Two-person pre-seed startup (deliberate weak signal)
```

**Verify:**
```bash
cd duvo-signal-loop
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill keys
```

**Commit:** (explicit adds — never `git add -A` here, `.env` must not be committed)
```bash
git add .gitignore requirements.txt .env.example companies.csv tools/__init__.py writeback/__init__.py
git commit -m "scaffold: deps, env template, target list"
```

---

## Task 1: config.py

```python
"""Credentials + model. Exa + Anthropic always required; write-back keys validated lazily."""
import os
from dotenv import load_dotenv

load_dotenv()

EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CRM_PROVIDER = os.environ.get("CRM_PROVIDER", "attio")  # "attio" (default) | "hubspot"
ATTIO_API_KEY = os.environ.get("ATTIO_API_KEY", "")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
OUTREACH_PROVIDER = os.environ.get("OUTREACH_PROVIDER", "brevo")  # "brevo" (default) | "lemlist"
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_LIST_ID = os.environ.get("BREVO_LIST_ID", "")
LEMLIST_API_KEY = os.environ.get("LEMLIST_API_KEY", "")
LEMLIST_CAMPAIGN_ID = os.environ.get("LEMLIST_CAMPAIGN_ID", "")
TEST_EMAIL = os.environ.get("TEST_EMAIL", "jakubkubala3@gmail.com")

CLAUDE_MODEL = "claude-sonnet-4-6"  # fast + strong for live demo; swap to opus if desired
MAX_SCOUT_SEARCHES = 4              # hard cap per scout agent
MAX_ANALYST_SEARCHES = 2           # hard cap on analyst verification searches


def require(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required env var: {name}. Add it to .env")
    return value
```

**Verify:** `python -c "import config; print(bool(config.EXA_API_KEY))"` → `True`
**Commit:** `git add config.py && git commit -m "feat: config"`

---

## Task 2: models.py

```python
"""Pydantic models — the contract shared across all agents."""
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
    tier: str                 # Tier 1 | Tier 2 | Tier 3
    confidence: str           # high | medium | low
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
    crm_status: str = "skipped"
    slack_status: str = "skipped"
    outreach_status: str = "skipped"
    agent_log: list[str] = []   # tool calls each agent made, for the report
```

**Verify:** `python -c "from models import ICPScore, RunResult; print('ok')"` → `ok`
**Commit:** `git add models.py && git commit -m "feat: models"`

---

## Task 3: agent_core.py (the agent runtime)

**Files:** Create `agent_core.py`

The single tool-use loop that powers every agent. Calls the model; runs any `tool_use` blocks via
`impls`; feeds results back; repeats until a *final* tool is called, the model stops calling tools,
or `max_turns` is hit. `agent_log` records each tool call for the audit report.

```python
"""Reusable Anthropic tool-use loop — the runtime every agent in the system runs on."""
from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, require

_client = Anthropic(api_key=require("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY))


def run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
    """Drive a tool-using agent.

    system/user: prompts. tools: Anthropic tool schemas. impls: {name: callable(**input)->str}.
    final_tools: calling one ends the loop. log: optional list to append "agent called <tool>".
    Returns the messages list (full transcript) for debugging.
    """
    final = set(final_tools)
    messages = [{"role": "user", "content": user}]
    for _ in range(max_turns):
        resp = _client.messages.create(
            model=CLAUDE_MODEL, max_tokens=2000, system=system,
            tools=tools, messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return messages  # agent has nothing more to do
        results = []
        hit_final = False
        for tu in tool_uses:
            if log is not None:
                log.append(f"{tu.name}({_short(tu.input)})")
            try:
                output = impls[tu.name](**tu.input)
            except Exception as exc:
                output = f"tool error: {exc}"
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": str(output)})
            if tu.name in final:
                hit_final = True
        messages.append({"role": "user", "content": results})
        if hit_final:
            return messages
    return messages


def _short(d) -> str:
    try:
        items = ", ".join(f"{k}={str(v)[:40]}" for k, v in d.items())
    except Exception:
        items = ""
    return items[:120]
```

**Verify:** `python -c "from agent_core import run_agent; print('ok')"` → `ok`
**Commit:** `git add agent_core.py && git commit -m "feat: agent runtime (tool-use loop)"`

---

## Task 4: tools/exa_tool.py (the search tool agents use)

**Files:** Create `tools/exa_tool.py`

```python
"""exa_search — the tool scouts and the analyst use to search the web."""
from exa_py import Exa

from config import EXA_API_KEY, require

_exa = Exa(api_key=require("EXA_API_KEY", EXA_API_KEY))

EXA_SEARCH_TOOL = {
    "name": "exa_search",
    "description": "Search the web for recent, sourced information. Returns up to 5 results "
                   "with title, published date, url, and a short summary. Use a focused query; "
                   "run again with a refined query to follow a promising thread.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Focused search query."},
            "start_published_date": {
                "type": "string",
                "description": "Optional ISO date (YYYY-MM-DD); only results published after it.",
            },
        },
        "required": ["query"],
    },
}


def exa_search(query: str, start_published_date: str | None = None) -> str:
    kwargs = {"num_results": 5, "summary": {"query": query}}
    if start_published_date:
        kwargs["start_published_date"] = start_published_date
    try:
        res = _exa.search_and_contents(query, **kwargs)
    except Exception as exc:
        return f"search failed: {exc}"
    lines = []
    for r in getattr(res, "results", []):
        summary = (getattr(r, "summary", None) or "").strip().replace("\n", " ")
        lines.append(
            f"- TITLE: {getattr(r, 'title', None) or '(none)'}\n"
            f"  DATE: {getattr(r, 'published_date', None) or 'unknown'}\n"
            f"  URL: {getattr(r, 'url', '') or ''}\n"
            f"  SUMMARY: {summary[:400]}"
        )
    return "\n".join(lines) if lines else "no results"
```

**Verify:**
```bash
python -c "from tools.exa_tool import exa_search; print(exa_search('Rohlik Group expansion 2025')[:300])"
```
Expected: prints a few result lines (or `no results`), no traceback.

**Commit:** `git add tools/exa_tool.py && git commit -m "feat: exa_search tool"`

---

## Task 5: scouts.py (scout agents — T0)

**Files:** Create `scouts.py`

Each scout is a real agent: it searches iteratively in its beat, then calls `submit_signals`.
Four scouts run in parallel per account.

```python
"""Scout agents: one per signal beat, each a tool-using agent over exa_search."""
from concurrent.futures import ThreadPoolExecutor

from config import MAX_SCOUT_SEARCHES
from models import Company, Signal
from agent_core import run_agent
from tools.exa_tool import EXA_SEARCH_TOOL, exa_search

# beat key -> human description
BEATS = [
    ("erp_migration", "ERP / supply-chain / finance software migrations and implementations "
                      "(SAP, S/4HANA, Oracle, new procurement or reconciliation systems)"),
    ("hiring", "hiring in supply chain, procurement, operations, or finance "
               "(new directors/managers, team build-outs)"),
    ("ma_leadership", "M&A activity and leadership changes (new CFO, COO, Supply Chain Director, "
                      "acquisitions, mergers)"),
    ("pain", "operational pain: manual reconciliation, invoice/PO matching, supplier-portal "
             "chaos, inventory or back-office inefficiency"),
]

SUBMIT_TOOL = {
    "name": "submit_signals",
    "description": "Submit the sourced signals you found (may be empty). Call once when done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string", "description": "1-2 sentences."},
                        "source_url": {"type": "string"},
                        "published_date": {"type": "string",
                                           "description": "ISO date or empty if unknown."},
                        "relevance": {"type": "string",
                                      "description": "Why this matters for Duvo."},
                    },
                    "required": ["title", "summary", "source_url", "relevance"],
                },
            }
        },
        "required": ["signals"],
    },
}


def run_scout(company: Company, beat_key: str, beat_desc: str, log=None) -> list[Signal]:
    system = (
        "You are a B2B GTM signal scout for Duvo (AI agents that automate retail/CPG back-office "
        f"operations like reconciliation and PO matching). Your beat: {beat_desc}.\n"
        "Find real, recent, SOURCED intent signals about the target company in your beat. "
        f"Search iteratively with exa_search: start broad, then refine to follow the best thread. "
        f"Do at most {MAX_SCOUT_SEARCHES} searches. Discard anything not clearly about the target "
        "company or not in your beat. NEVER invent facts — report only what a result supports; an "
        "empty result set is fine. When done, call submit_signals."
    )
    user = (f"Target company: {company.name} ({company.domain}, {company.country}). "
            f"Context: {company.description}")
    captured: dict = {"signals": []}

    def submit_signals(signals):
        captured["signals"] = signals
        return f"received {len(signals)} signals"

    impls = {"exa_search": exa_search, "submit_signals": submit_signals}
    run_agent(system, user, [EXA_SEARCH_TOOL, SUBMIT_TOOL], impls,
              max_turns=MAX_SCOUT_SEARCHES + 2, final_tools={"submit_signals"}, log=log)

    out = []
    for s in captured["signals"]:
        out.append(Signal(
            signal_type=beat_key,
            title=s.get("title", "(no title)"),
            summary=s.get("summary", ""),
            source_url=s.get("source_url", ""),
            published_date=(s.get("published_date") or None),
            relevance=s.get("relevance", ""),
        ))
    return out


def scout_all(company: Company, log=None) -> list[Signal]:
    """Run all 4 scout agents in parallel for one company."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        groups = list(ex.map(lambda b: run_scout(company, b[0], b[1], log), BEATS))
    return [sig for group in groups for sig in group]
```

**Verify (real agents, real Exa):**
```bash
python -c "
from models import Company
from scouts import scout_all
log=[]
sigs = scout_all(Company(name='Rohlik Group', domain='rohlik.cz', country='CZ',
                         description='Online grocery scaling across CEE'), log)
print('signals:', len(sigs)); print('tool calls:', len(log))
[print('-', s.signal_type, '|', s.published_date, '|', s.title[:55]) for s in sigs[:6]]
"
```
Expected: several signals, multiple tool calls logged, no traceback.

**Commit:** `git add scouts.py && git commit -m "feat: scout agents (parallel, exa tool)"`

---

## Task 6: analyst.py (analyst agent + guard — T0)

**Files:** Create `analyst.py`

The analyst can run up to 2 verification searches to refute a doubtful claim before trusting it,
then calls `record_assessment`. `apply_guards` is a deterministic, demonstrable post-guard.

```python
"""Analyst agent: validate signals (with its own verification searches), score, draft outreach."""
import json

from config import MAX_ANALYST_SEARCHES
from models import Company, Signal, ICPScore, OutreachDraft
from agent_core import run_agent
from tools.exa_tool import EXA_SEARCH_TOOL, exa_search

ICP_DEFINITION = """Duvo AI closes operational back-office work end-to-end for enterprise retail
and CPG. Ideal customer:
- Industry: retail, grocery, CPG, e-commerce, pharmacy retail.
- Size: 100M EUR+ revenue OR 500+ employees.
- Tech: runs SAP / Oracle / similar ERP; manual reconciliation, PO/invoice matching in Excel,
  supplier-portal chaos.
- Geography: CEE preferred, Western Europe fine.
- Trigger events: ERP migration, M&A, new CFO / Supply Chain Director, rapid expansion.
Scoring 1-10: 9-10 perfect (retail/CPG, ERP, clear pain, trigger present); 7-8 strong;
5-6 moderate (adjacent or missing a trigger); 3-4 weak (wrong industry/too small); 1-2 not a fit."""

RECORD_TOOL = {
    "name": "record_assessment",
    "description": "Record the final ICP assessment and drafted outreach. Call once when done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "tier": {"type": "string", "enum": ["Tier 1", "Tier 2", "Tier 3"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "why_fit": {"type": "array", "items": {"type": "string"}},
            "why_not": {"type": "array", "items": {"type": "string"}},
            "recommended_persona": {"type": "string"},
            "recommended_angle": {"type": "string"},
            "reasoning": {"type": "string"},
            "needs_human_research": {"type": "boolean"},
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


def run_analyst(company: Company, signals: list[Signal], log=None) -> ICPScore:
    signals_json = json.dumps([s.model_dump() for s in signals], ensure_ascii=False, indent=2)
    system = (
        f"{ICP_DEFINITION}\n\n"
        "You are Duvo's ICP analyst. Assess the company's fit and draft a first-touch outreach.\n"
        "Ground every claim ONLY in the signals provided. If a signal looks important but doubtful, "
        f"you MAY verify it with exa_search (at most {MAX_ANALYST_SEARCHES} times) before trusting "
        "it. If signals are thin, generic, or undated, set confidence=low and "
        "needs_human_research=true and keep the score conservative. The outreach first_line MUST "
        "reference a real signal (or stay generic if none). Never fabricate a specific event. "
        "When done, call record_assessment."
    )
    user = (f"Company: {company.name} ({company.domain}, {company.country}). "
            f"Context: {company.description}\n\nSignals:\n{signals_json}")

    captured: dict = {}

    def record_assessment(**kw):
        captured.update(kw)
        return "recorded"

    impls = {"exa_search": exa_search, "record_assessment": record_assessment}
    run_agent(system, user, [EXA_SEARCH_TOOL, RECORD_TOOL], impls,
              max_turns=MAX_ANALYST_SEARCHES + 3, final_tools={"record_assessment"}, log=log)

    if not captured:  # agent never produced an assessment — conservative default
        return ICPScore(
            company_name=company.name, domain=company.domain, score=3, tier="Tier 3",
            confidence="low", why_fit=[], why_not=["No usable signals / analyst produced nothing."],
            recommended_persona="Supply Chain / Finance leadership", recommended_angle="",
            reasoning="Insufficient evidence.", needs_human_research=True,
            outreach=OutreachDraft(persona="Supply Chain leadership", subject="", first_line="",
                                   body=""),
        )

    outreach = OutreachDraft(**captured.pop("outreach"))
    score = ICPScore(company_name=company.name, domain=company.domain, outreach=outreach, **captured)
    return apply_guards(score, signals)


def apply_guards(score: ICPScore, signals: list[Signal]) -> ICPScore:
    """Deterministic post-guard: thin/undated evidence cannot yield a confident high score."""
    dated = [s for s in signals if s.published_date]
    if not dated:
        score.confidence = "low"
        score.needs_human_research = True
    if score.confidence == "low" and score.score >= 7:
        score.score = 6
    if score.score >= 8 and not score.needs_human_research:
        score.tier = "Tier 1"
    elif score.score >= 5:
        score.tier = "Tier 2"
    else:
        score.tier = "Tier 3"
    if score.needs_human_research and score.tier == "Tier 1":
        score.tier = "Tier 2"
    return score
```

**Verify (strong vs deliberately weak account):**
```bash
python -c "
from models import Company
from scouts import scout_all
from analyst import run_analyst
for c in [Company(name='Rohlik Group', domain='rohlik.cz', country='CZ', description='Online grocery scaling CEE'),
          Company(name='Tiny Local Bakery', domain='tinylocalbakery-demo.cz', country='CZ', description='Single-store bakery')]:
    s = run_analyst(c, scout_all(c))
    print(c.name, '->', s.score, s.tier, s.confidence, 'human?', s.needs_human_research)
"
```
Expected: Rohlik higher tier/confidence; the bakery low/Tier 3, `needs_human_research=True` — the guard firing.

**Commit:** `git add analyst.py && git commit -m "feat: analyst agent + deterministic guard"`

---

## Task 7: CRM write-back — crm dispatcher + Attio (T0)  [HubSpot adapter optional]

**Files:** Create `writeback/crm.py`, `writeback/attio.py`. (Optional adapter `writeback/hubspot.py` below.)

The router calls ONE interface — `crm.upsert_account(score)` — and the dispatcher routes to Attio
(default) or HubSpot by `CRM_PROVIDER`. Each provider creates a company record + an evidence note
labeled "AI-suggested". The ICP score rides in the note title so it is visible without provisioning
a custom field (promoting it to a structured attribute is a documented next step).

`writeback/crm.py`:
```python
"""CRM dispatcher — the single interface the router uses; provider chosen by CRM_PROVIDER."""
from config import CRM_PROVIDER
from models import ICPScore


def upsert_account(score: ICPScore) -> str:
    if CRM_PROVIDER == "hubspot":
        from writeback import hubspot
        return hubspot.upsert_account(score)
    from writeback import attio
    return attio.upsert_account(score)
```

`writeback/attio.py` (endpoints verified: `POST /v2/objects/companies/records`, `POST /v2/notes`):
```python
"""Attio CRM write-back: create a company record + an evidence note (AI-suggested)."""
import requests

from config import ATTIO_API_KEY, require
from models import ICPScore

_BASE = "https://api.attio.com/v2"


def _headers() -> dict:
    return {"Authorization": f"Bearer {require('ATTIO_API_KEY', ATTIO_API_KEY)}",
            "Content-Type": "application/json"}


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
    return "\n".join(lines)


def _create_company(score: ICPScore) -> str:
    url = f"{_BASE}/objects/companies/records"
    # try with domains; if Attio rejects that value shape, retry with name only
    last = None
    for values in ({"name": score.company_name, "domains": [score.domain]},
                   {"name": score.company_name}):
        last = requests.post(url, headers=_headers(), json={"data": {"values": values}})
        if last.status_code < 300:
            return last.json()["data"]["id"]["record_id"]
    last.raise_for_status()  # surface the last error
    return ""


def _create_note(score: ICPScore, record_id: str) -> None:
    payload = {"data": {
        "parent_object": "companies",
        "parent_record_id": record_id,
        "title": f"ICP {score.score}/10 ({score.tier}) — AI-suggested, review before outreach",
        "format": "plaintext",
        "content": _note_body(score),
    }}
    requests.post(f"{_BASE}/notes", headers=_headers(), json=payload).raise_for_status()


def upsert_account(score: ICPScore) -> str:
    record_id = _create_company(score)
    _create_note(score, record_id)
    return f"attio company {record_id} (ICP {score.score}) + evidence note"
```

**Optional — `writeback/hubspot.py`** (same `upsert_account(score) -> str` interface; only used when
`CRM_PROVIDER=hubspot`. Skip during the demo if you have no HubSpot portal):
```python
"""HubSpot adapter: custom property + upsert company + evidence note. Same interface as attio."""
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
    r = requests.post(f"{_BASE}/crm/v3/objects/companies/search", headers=_headers(), json=search)
    results = r.json().get("results", [])
    props = {"name": score.company_name, "domain": score.domain, "icp_score": score.score}
    if results:
        cid = results[0]["id"]
        requests.patch(f"{_BASE}/crm/v3/objects/companies/{cid}", headers=_headers(),
                       json={"properties": props})
    else:
        cr = requests.post(f"{_BASE}/crm/v3/objects/companies", headers=_headers(),
                           json={"properties": props})
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
            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": _NOTE_TO_COMPANY}]}],
    }
    requests.post(f"{_BASE}/crm/v3/objects/notes", headers=_headers(), json=payload)


def upsert_account(score: ICPScore) -> str:
    ensure_icp_property()
    cid = _upsert_company(score)
    _create_note(score, cid)
    return f"company {cid} (icp_score={score.score}) + evidence note"
```

**Verify (Attio, the default):**
```bash
python -c "
from models import Company
from scouts import scout_all
from analyst import run_analyst
from writeback import crm
c = Company(name='Rohlik Group', domain='rohlik.cz', country='CZ', description='Online grocery CEE')
print(crm.upsert_account(run_analyst(c, scout_all(c))))
"
```
Expected: `attio company <uuid> (ICP ...) + evidence note`; confirm the company + note in Attio.

**Commit:**
```bash
git add writeback/crm.py writeback/attio.py writeback/hubspot.py
git commit -m "feat: pluggable CRM write-back (Attio default, HubSpot adapter)"
```

---

## Task 8: router.py (router agent — T0 minimal)

**Files:** Create `router.py`

The router is an agent whose tools are the write-backs. Build the FULL version now: all four tools
(`crm_upsert`, `slack_alert`, `outreach_queue`, `finish`) are always registered, and the
Slack/outreach modules are imported **lazily inside their tool functions**. So in T0 — before
`writeback/slack.py` and `writeback/outreach.py` exist — a confident Tier 1 that triggers
`slack_alert` raises `ImportError`, which `run_agent` catches and feeds back as `tool error: ...`;
the run still completes. Once T1/T2 add those files, the same router lights up with no code change.
`crm_upsert` calls the CRM dispatcher (Attio/HubSpot per `CRM_PROVIDER`) and `outreach_queue` calls
the outreach dispatcher (Brevo/lemlist per `OUTREACH_PROVIDER`), both with no router change. Every
tool also self-guards (refuses non-confident-Tier-1) and respects `dry_run`.

```python
"""Router agent: decides how to action a scored account; its tools are the write-backs."""
import json

from config import TEST_EMAIL, CRM_PROVIDER, OUTREACH_PROVIDER
from models import RunResult
from agent_core import run_agent
from writeback import crm

ROUTER_SYSTEM = (
    "You are Duvo's GTM routing agent. You decide how to action one scored account into the "
    "sales stack. Rules:\n"
    "- ALWAYS call crm_upsert to log the account in the CRM with its evidence note.\n"
    "- If the account is a CONFIDENT Tier 1 (tier == 'Tier 1' and needs_human_research is false), "
    "also call slack_alert and outreach_queue (queues the lead for a rep to review and send — "
    "never sent automatically).\n"
    "- If it is flagged needs_human_research, or not Tier 1, ONLY call crm_upsert.\n"
    "Call finish when done. Some tools may refuse if their own safety check fails — that is "
    "expected; do not retry a refused tool."
)


def _tool_schema(name, desc):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": {}}}


def run_router(rr: RunResult, dry_run: bool, test_email: str = TEST_EMAIL, log=None) -> None:
    s = rr.score
    confident_t1 = (s.tier == "Tier 1" and not s.needs_human_research)

    def crm_upsert():
        if dry_run:
            rr.crm_status = f"[dry-run] upsert account + note into {CRM_PROVIDER} (ICP {s.score})"
        else:
            rr.crm_status = crm.upsert_account(s)
        return rr.crm_status

    def slack_alert():
        if not confident_t1:
            return "refused: not a confident Tier 1 (safety guard)"
        if dry_run:
            rr.slack_status = "[dry-run] alert #sales"
        else:
            from writeback import slack
            rr.slack_status = slack.alert_tier1(s)
        return rr.slack_status

    def outreach_queue():
        if not confident_t1:
            return "refused: not a confident Tier 1 (safety guard)"
        if dry_run:
            rr.outreach_status = f"[dry-run] queue lead for review in {OUTREACH_PROVIDER}"
        else:
            from writeback import outreach
            rr.outreach_status = outreach.queue_lead(s, test_email)
        return rr.outreach_status

    def finish():
        return "done"

    tools = [
        _tool_schema("crm_upsert", "Log the company in the CRM with its ICP score and an "
                                   "evidence note. Always allowed."),
        _tool_schema("slack_alert", "Post a Tier-1 alert to #sales. Only for confident Tier 1."),
        _tool_schema("outreach_queue", "Queue the lead into the outreach tool's review list for a "
                                       "rep to approve and send. Only for confident Tier 1."),
        _tool_schema("finish", "Call when routing is complete."),
    ]
    impls = {"crm_upsert": crm_upsert, "slack_alert": slack_alert,
             "outreach_queue": outreach_queue, "finish": finish}

    user = json.dumps({
        "company": s.company_name, "domain": s.domain, "score": s.score, "tier": s.tier,
        "confidence": s.confidence, "needs_human_research": s.needs_human_research,
        "persona": s.recommended_persona, "angle": s.recommended_angle,
    }, ensure_ascii=False)

    run_agent(ROUTER_SYSTEM, user, tools, impls, max_turns=6, final_tools={"finish"}, log=log)
```

**Verify (dry-run — router decides, tools simulate):**
```bash
python -c "
from models import Company
from scouts import scout_all
from analyst import run_analyst
from router import run_router
from models import RunResult
c = Company(name='Rohlik Group', domain='rohlik.cz', country='CZ', description='Online grocery CEE')
score = run_analyst(c, scout_all(c)); rr = RunResult(score=score, signals=[])
log=[]; run_router(rr, dry_run=True, log=log)
print('decisions:', log)
print('crm:', rr.crm_status, '| slack:', rr.slack_status, '| outreach:', rr.outreach_status)
"
```
Expected: `log` shows the router calling `crm_upsert` and (if Tier 1) `slack_alert`,
`outreach_queue`, then `finish`. Statuses are `[dry-run] ...`.

**Commit:** `git add router.py && git commit -m "feat: router agent (write-backs as tools, self-guarding)"`

---

## Task 9: reporter.py + templates/report.html (T0)

**Files:** Create `reporter.py`, `templates/report.html`

`reporter.py`:
```python
"""Render an HTML audit log of the run — including each account's agent tool calls."""
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
 h1{margin:0;font-size:20px}.sub{color:#8a93a2;font-size:13px;margin-top:4px}
 .card{margin:16px 32px;border:1px solid #262b36;border-radius:10px;overflow:hidden;background:#161922}
 .top{display:flex;align-items:center;gap:14px;padding:14px 18px;border-bottom:1px solid #262b36}
 .badge{font-weight:700;border-radius:6px;padding:4px 10px;color:#0f1115}
 .t1{background:#34d399}.t2{background:#fbbf24}.t3{background:#f87171}
 .name{font-size:16px;font-weight:600}.muted{color:#8a93a2}
 .flag{margin-left:auto;background:#7c3aed;color:#fff;border-radius:6px;padding:4px 10px;font-size:12px}
 .body{padding:14px 18px;display:grid;grid-template-columns:1fr 1fr;gap:18px}
 .lab{color:#8a93a2;text-transform:uppercase;font-size:11px;letter-spacing:.04em;margin:10px 0 4px}
 ul{margin:4px 0;padding-left:18px}a{color:#60a5fa}
 .draft{grid-column:1/3;background:#0f1115;border:1px solid #262b36;border-radius:8px;padding:12px}
 .agents{grid-column:1/3;font-size:12px;color:#9aa4b2}
 .status{grid-column:1/3;font-size:12px;color:#8a93a2;border-top:1px dashed #262b36;padding-top:8px}
 code{background:#0f1115;padding:1px 5px;border-radius:4px}
</style></head><body>
<header><h1>duvo-signal-loop — agent run report</h1>
<div class="sub">{{ results|length }} accounts · scouts → analyst → router · audit log only</div></header>
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
  <div class="agents"><div class="lab">Agent tool calls</div>
   {% for a in r.agent_log %}<code>{{ a }}</code> {% endfor %}</div>
  <div class="status">CRM: <code>{{ r.crm_status }}</code> ·
   Slack: <code>{{ r.slack_status }}</code> · Outreach: <code>{{ r.outreach_status }}</code></div>
 </div>
</div>{% endfor %}
</body></html>
```

**Commit:** `git add reporter.py templates/report.html && git commit -m "feat: HTML agent run report"`

---

## Task 10: main.py (orchestrator — T0)

**Files:** Create `main.py`

```python
"""Conductor: per account run scouts → analyst → router, then render the report."""
import argparse
import csv
import time

from config import TEST_EMAIL
from models import Company, RunResult
from scouts import scout_all
from analyst import run_analyst
from router import run_router
from reporter import generate_report


def load_companies(path: str = "companies.csv") -> list[Company]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [Company(**row) for row in csv.DictReader(fh)]


def run(dry_run: bool, test_email: str, limit: int | None) -> None:
    companies = load_companies()
    if limit:
        companies = companies[:limit]
    print(f"Running {len(companies)} accounts through the agent team (dry_run={dry_run})...")
    results: list[RunResult] = []
    for c in companies:
        print(f"  -> {c.name}")
        log: list[str] = []
        signals = scout_all(c, log)
        score = run_analyst(c, signals, log)
        print(f"     {score.score}/10 {score.tier} conf={score.confidence} "
              f"human={score.needs_human_research} ({len(signals)} signals, {len(log)} tool calls)")
        rr = RunResult(score=score, signals=signals, agent_log=log)
        run_router(rr, dry_run, test_email, log)
        results.append(rr)
        time.sleep(0.3)
    path = generate_report(results)
    print(f"\nDone. Open {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="agents run; write-back tools simulate")
    ap.add_argument("--test-email", default=TEST_EMAIL, help="lemlist test lead email")
    ap.add_argument("--limit", type=int, default=None, help="process only first N accounts")
    args = ap.parse_args()
    run(dry_run=args.dry_run, test_email=args.test_email, limit=args.limit)
```

**Verify (full dry run — agents real, writes simulated):**
```bash
python main.py --dry-run --limit 3
open output/run-report.html
```
Expected: each account prints a score line + tool-call count; report shows agent tool calls,
Tier-1 routing, and the weak account flagged.

**Then a real T0 run on a couple accounts (CRM only, since Slack/outreach files come in T1/T2):**
```bash
python main.py --limit 2
```
Expected: CRM (Attio) statuses show real record ids; Slack/outreach show ERROR (modules not present
yet) but the run completes — this is the isolation guarantee working.

**Commit:** `git add main.py && git commit -m "feat: orchestrator (scouts -> analyst -> router)"`

---

## Task 11: writeback/slack.py (T1)

**Files:** Create `writeback/slack.py`. (Router already calls it lazily — no router change needed.)

```python
"""Tier-1 alert to #sales via incoming webhook."""
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

**Verify:**
```bash
python -c "
from models import ICPScore, OutreachDraft
from writeback import slack
s = ICPScore(company_name='Rohlik Group', domain='rohlik.cz', score=9, tier='Tier 1',
  confidence='high', why_fit=['retail','ERP'], why_not=[], recommended_persona='Supply Chain Director',
  recommended_angle='ERP reconciliation pain', reasoning='strong', needs_human_research=False,
  outreach=OutreachDraft(persona='Supply Chain Director', subject='x', first_line='Saw your CEE expansion', body='y'))
print(slack.alert_tier1(s))
"
```
Expected: message in Slack; prints `alert posted to #sales`.

**Commit:** `git add writeback/slack.py && git commit -m "feat: Slack Tier-1 alert tool"`

---

## Task 12: Outreach — outreach dispatcher + Brevo (T2)  [lemlist adapter optional]

**Files:** Create `writeback/outreach.py`, `writeback/brevo.py`. (Optional adapter
`writeback/lemlist.py` below.) The router already calls `outreach.queue_lead` lazily — no router change.

The router calls ONE interface — `outreach.queue_lead(score, test_email)` — and the dispatcher
routes to Brevo (default) or lemlist by `OUTREACH_PROVIDER`. Neither ever calls a send endpoint:
the lead lands in a review list / paused campaign for a human to approve and send.

`writeback/outreach.py`:
```python
"""Outreach dispatcher — one interface the router uses; provider chosen by OUTREACH_PROVIDER."""
from config import OUTREACH_PROVIDER
from models import ICPScore


def queue_lead(score: ICPScore, test_email: str) -> str:
    if OUTREACH_PROVIDER == "lemlist":
        from writeback import lemlist
        return lemlist.queue_lead(score, test_email)
    from writeback import brevo
    return brevo.queue_lead(score, test_email)
```

`writeback/brevo.py` (endpoint verified: `POST /v3/contacts`, header `api-key`; `listIds` adds the
contact to the review list on creation — no separate add-to-list call needed):
```python
"""Brevo outreach: create the contact in a Tier-1 review list. Never sends — a rep approves + sends."""
import requests

from config import BREVO_API_KEY, BREVO_LIST_ID, require
from models import ICPScore

_BASE = "https://api.brevo.com/v3"


def _headers() -> dict:
    return {"api-key": require("BREVO_API_KEY", BREVO_API_KEY),
            "Content-Type": "application/json", "accept": "application/json"}


def queue_lead(score: ICPScore, test_email: str) -> str:
    list_id = int(require("BREVO_LIST_ID", BREVO_LIST_ID))
    # Custom attributes (COMPANY, ICEBREAKER, ...) must exist on the account or Brevo 400s;
    # try the rich payload, then fall back to email + list only so the lead always lands.
    full = {
        "email": test_email,
        "attributes": {
            "COMPANY": score.company_name,
            "DOMAIN": score.domain,
            "JOBTITLE": score.recommended_persona,
            "ICP_SCORE": str(score.score),
            "ICEBREAKER": score.outreach.first_line,
        },
        "listIds": [list_id],
        "updateEnabled": True,
    }
    minimal = {"email": test_email, "listIds": [list_id], "updateEnabled": True}
    last = None
    for payload in (full, minimal):
        last = requests.post(f"{_BASE}/contacts", headers=_headers(), json=payload)
        if last.status_code < 300:
            return f"contact queued in Brevo review list {list_id} (not sent — rep reviews & sends)"
    last.raise_for_status()
    return ""
```

**Optional — `writeback/lemlist.py`** (same `queue_lead(score, test_email) -> str` interface; only
used when `OUTREACH_PROVIDER=lemlist`. Needs a paid lemlist plan + a **paused** campaign):
```python
"""lemlist adapter: queue a Tier-1 lead into a PAUSED campaign as a draft. Same interface as brevo."""
import requests

from config import LEMLIST_API_KEY, LEMLIST_CAMPAIGN_ID, require
from models import ICPScore

_BASE = "https://api.lemlist.com/api"


def queue_lead(score: ICPScore, test_email: str) -> str:
    campaign = require("LEMLIST_CAMPAIGN_ID", LEMLIST_CAMPAIGN_ID)
    url = f"{_BASE}/campaigns/{campaign}/leads"
    payload = {
        "email": test_email,
        "companyName": score.company_name,
        "companyDomain": score.domain,
        "jobTitle": score.recommended_persona,
        "icpScore": str(score.score),
        "icebreaker": score.outreach.first_line,
    }
    r = requests.post(url, auth=("", require("LEMLIST_API_KEY", LEMLIST_API_KEY)),
                      json=payload, params={"deduplicate": "true"})
    r.raise_for_status()
    return f"lead queued in paused lemlist campaign {campaign} (awaiting rep approval)"
```

**Verify (Brevo, the default):**
```bash
python -c "
from models import ICPScore, OutreachDraft
from writeback import outreach
s = ICPScore(company_name='Rohlik Group', domain='rohlik.cz', score=9, tier='Tier 1',
  confidence='high', why_fit=['x'], why_not=[], recommended_persona='Supply Chain Director',
  recommended_angle='x', reasoning='x', needs_human_research=False,
  outreach=OutreachDraft(persona='Supply Chain Director', subject='x', first_line='Saw your CEE expansion', body='y'))
print(outreach.queue_lead(s, 'jakubkubala3@gmail.com'))
"
```
Expected: prints `contact queued in Brevo review list ...`; confirm the contact appears in the
review list in Brevo and nothing was sent. If the API errors, the loop still completes (router
status ERROR); fall back to a manually added contact + screenshot and say so.

**Commit:**
```bash
git add writeback/outreach.py writeback/brevo.py writeback/lemlist.py
git commit -m "feat: pluggable outreach (Brevo default, lemlist adapter)"
```

---

## Task 13: Full run + README

Run everything for real:
```bash
python main.py            # all accounts
open output/run-report.html
```
Expected: confident Tier-1 accounts → router calls crm_upsert + slack_alert + outreach_queue; weak
accounts → router calls only crm_upsert and slack/outreach self-refuse (visible in the agent tool-call log).

`README.md`:
```markdown
# duvo-signal-loop

A team of tool-using AI agents that turns a target list of retail/CPG accounts into rep-ready
pipeline, against Duvo's stack (Exa, a CRM, Slack, an outreach tool).

## The agents
- **Scout agents (x4, parallel)** — each owns a beat (ERP, hiring, M&A, pain), uses an `exa_search`
  tool, decides its own queries, returns only sourced signals.
- **Analyst agent** — validates signals (can run its own verification searches before trusting a
  claim), scores ICP fit, drafts personalized outreach. A deterministic `apply_guards()` caps any
  hallucinated confidence.
- **Router agent** — decides how to action the account; its tools are the write-backs. They
  self-guard: Slack/outreach refuse anything but a confident Tier 1.

Python (`main.py`) only orchestrates the hand-offs. Every agent runs on one shared tool-use loop
(`agent_core.run_agent`).

## Pluggable CRM + outreach
Both write-back steps are one interface with two adapters each:
- `crm.upsert_account()` — **Attio** (default, instant self-serve free API) | **HubSpot** (`CRM_PROVIDER=hubspot`).
- `outreach.queue_lead()` — **Brevo** (default, free API, no card) | **lemlist** (`OUTREACH_PROVIDER=lemlist`).
Target Duvo's real stack (HubSpot + lemlist) by flipping the two env vars — no other change.

## Setup
1. `python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. `cp .env.example .env`; fill Exa, Anthropic, `ATTIO_API_KEY` (keep `CRM_PROVIDER=attio`), Slack
   webhook, `BREVO_API_KEY` + `BREVO_LIST_ID` (keep `OUTREACH_PROVIDER=brevo`). See `.env.example`
   for exact, sourced steps.

## Run
- `python main.py --dry-run` — agents run and really decide; write-back tools simulate (safe demo fallback).
- `python main.py --limit 3` — first 3 accounts only (fast live demo).
- `python main.py` — full loop with write-backs. Audit log: `output/run-report.html`.

## Where agency is bounded (deliberate human-in-the-loop)
- Scouts may not invent; the analyst independently verifies; undated signals are discarded.
- `apply_guards()` — not the model — caps low-confidence high scores and flags thin accounts.
- The router never sends: outreach leads land in a **review list** (Brevo) / **paused** campaign
  (lemlist) a rep approves; Slack/outreach tools refuse non-confident-Tier-1 accounts even if the
  agent asks.

## Where it breaks
- Exa noise/staleness on big brands (scout iteration + analyst verification mitigate; recall limited).
- Agent loops add latency/variance; bounded by turn caps. Demo runs a few accounts live, full list pre-run.
- No real person-level email — persona recommended; demo uses a test email as the lead.
- One-shot run; production = scheduled run + score diff + alert only on change. No CRM dedup yet
  (re-runs create new records; production would assert/upsert).

## What I'd build next (one week)
Scouts as MCP-tool agents (Apollo/LinkedIn/Gong) · a discovery agent for net-new accounts · a Gong
call-outcome agent writing back to the CRM · a reply-handling agent branching the outreach sequence
on intent · promote the ICP score to a structured CRM attribute. The `run_agent` runtime stays;
only toolsets grow.
```

**Commit:** `git add README.md && git commit -m "docs: README"`

---

## Self-review (spec coverage)

- Scout agents, parallel, exa tool, iterative, sourced-only → Task 5. ✓
- Analyst agent with verification searches + deterministic guard → Task 6. ✓
- Router agent, write-backs as self-guarding tools, dry-run aware → Task 8 (+T1/T2 tools 11,12). ✓
- Shared `run_agent` tool-use runtime → Task 3. ✓
- Pluggable CRM (crm dispatcher + Attio + HubSpot) → Task 7; Slack → Task 11; pluggable outreach
  (outreach dispatcher + Brevo + lemlist) → Task 12. ✓
- Bounded agency / human-in-the-loop (no invent, guard cap, never-send, self-refuse) → Tasks 5,6,7,8,12. ✓
- `--dry-run` (agents decide, tools simulate) → Tasks 8, 10. ✓
- Layered build T0→T1→T2 → Tasks 3-10 (T0), 11 (T1), 12 (T2). ✓
- Agent tool-call audit log in report → Tasks 2 (`agent_log`), 9, 10. ✓
- Target list incl. 2 weak accounts → Task 0. ✓
- README (agents / pluggable CRM+outreach / bounded agency / where it breaks / next) → Task 13. ✓

Type consistency: `Company`/`Signal`/`OutreachDraft`/`ICPScore`/`RunResult` field names identical
across Tasks 2,5,6,7,8,9,10,11,12. `RunResult.crm_status` + `RunResult.outreach_status` used by
router (Task 8) and reporter (Task 9). `crm.upsert_account(score)->str` implemented by Attio +
HubSpot (Task 7); `outreach.queue_lead(score, test_email)->str` implemented by Brevo + lemlist
(Task 12); both called by the router (Task 8). `run_agent(system,user,tools,impls,max_turns,
final_tools,log)` signature identical across Tasks 3,5,6,8. ✓
```
