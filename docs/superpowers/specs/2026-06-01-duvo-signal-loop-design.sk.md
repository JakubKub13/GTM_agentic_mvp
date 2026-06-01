# duvo-signal-loop — Návrhový spec (multi-agent)

**Dátum:** 2026-06-01
**Autor:** Jakub Kubala
**Kontext:** Take-home na rolu GTM Engineer v Duvo.ai (CEO Tomáš Čupr). Postaviť niečo, čo
opraví pomalý/manuálny B2B GTM loop, reálne beží a dá sa naživo „poke-núť". Stack, proti ktorému
stavať: HubSpot, lemlist, Gong, Exa. Časový rozpočet: 3–4 hodiny. Výstup: repo + Loom + jednostranová
poznámka. Odovzdať do konca dňa v utorok 2026-06-02.

## Jednou vetou

Malý **tím AI agentov**, ktorý z cieľového zoznamu retail/CPG účtov vyrobí pipeline pripravený pre
repa. Na každý účet: paralelní **scout agenti** (každý s Exa search nástrojom) lovia zdrojované
intent signály, **analyst agent** ich validuje — pochybné tvrdenia si pred uverením overí vlastnými
hľadaniami — ohodnotí ICP fit a napíše personalizovaný outreach, a **router agent** sa rozhodne, ako
účet zaviesť do reálneho stacku (CRM, Slack, outreach nástroj). Python len orchestruje odovzdávky;
každý uzol je ozajstný tool-using agent, nie napevno zadrátovaný dopyt.

CRM aj outreach krok sú **pluggable interfacy**. `crm.upsert_account` beží proti **Attio** (okamžité
self-serve CRM API) a má **HubSpot** adaptér za rovnakým rozhraním; `outreach.queue_lead` beží proti
**Brevo** (free API, bez karty) a má **lemlist** adaptér za rovnakým rozhraním. Reálny stack Duva
(HubSpot + lemlist) je jeden riadok konfigurácie navyše (`CRM_PROVIDER`, `OUTREACH_PROVIDER`), keď
tie účty existujú — postavené na Attio + Brevo, aby sa demo sprovoznilo za minúty, nie na
registráciách, ktoré môžu uviaznuť.

## Prečo agenti, nie workflow (návrhová téza)

Každý scout je ozajstná agentická slučka: sám rozhoduje o svojich dopytoch, sleduje najsľubnejšie
vlákno a skončí, keď má dosť. Analyst si vie vyslať vlastné overovacie hľadania, aby tvrdenie vyvrátil
predtým, než mu uverí. Router uvažuje, aké akcie si účet zaslúži. Toto je tvar, ktorý Tomáš už
schválil na Rohlík briefing agentovi — paralelní tool-using zberači do validujúceho synthesizera so
strážami proti vymýšľaniu, fanúce sa per príjemca — prerobený proti stacku Duva.

**Ale agentita je zámerne ohraničená.** Agenti vlastnia *vratné, málo rizikové* rozhodnutia (ktorý
dopyt spustiť, na ktorú personu cieliť, ktorý kanál použiť). *Nevratné* kroky — zastropovanie
halucinovaného skóre a nikdy neposlať studený e-mail — sú tvrdé deterministické stráže a ľudská
schvaľovacia brána, nie veci, pri ktorých sa spolieha, že to agent trafí. Vedieť, kde agentitu
*zadržať*, je ten istý úsudok ako vedieť, kde nechať človeka v loope.

## Architektúra

Na každý účet Python orchestruje tri agentické fázy:

```
companies.csv
   │  na každý účet — Python orchestrátor (dirigent)
   ▼
┌── SCOUT AGENTI (4, paralelne) ────────────────────────┐
│  beaty: erp_migration · hiring · ma_leadership · pain │  každý = Claude agent
│  nástroj: exa_search                                   │  s ReAct nástrojovou
│  slučka: zvoľ dopyt → hľadaj → čítaj → spresni → opakuj│  slučkou (ohraničená)
│  koniec: submit_signals(...)  ← len ZDROJOVANÉ signály │
└────────────────────────────────────────────────────────┘
   ▼  všetky signály
ANALYST AGENT
   nástroje: exa_search (overenie), record_assessment
   - smie spustiť až 2 overovacie hľadania na vyvrátenie pochybného tvrdenia
   - výstup: skóre, tier, confidence, persona, angle, outreach draft
   ▼  + deterministický apply_guards()  (stropuje halucinované confidence)
ROUTER AGENT
   nástroje: crm_upsert, slack_alert, outreach_queue, finish
   - rozhodne, ktoré akcie si účet zaslúži
   - crm_upsert      -> pluggable CRM (Attio default, HubSpot adaptér za rovnakým rozhraním)
   - outreach_queue  -> pluggable outreach (Brevo default, lemlist adaptér za rovnakým rozhraním)
   - nástroje SA SAMÉ BRÁNIA: slack/outreach odmietnu, ak nejde o confident Tier 1
   ▼
output/run-report.html   (audit log: tool-calls každého agenta — pre Loom, nie deliverable)
```

## Agentický runtime

Jeden znovupoužiteľný helper `run_agent(system, user, tools, impls, max_turns, final_tools)`
implementuje Anthropic tool-use slučku: zavolaj model → vykonaj prípadné tool_use bloky → vráť
výsledky späť → opakuj, kým agent nezavolá určený *finálny* nástroj alebo neskončí. Každý agent v
systéme je jedno volanie tohto helpera s inou sadou nástrojov. To drží koncept „agenta" čestný
(modelom riadené použitie nástrojov v slučke) a kódovú bázu malú a auditovateľnú.

## Komponenty

| Súbor | Zodpovednosť | Agent? |
|------|----------------|--------|
| `config.py` | Env kľúče, id modelu | — |
| `models.py` | Pydantic: `Company`, `Signal`, `OutreachDraft`, `ICPScore`, `RunResult` | — |
| `agent_core.py` | `run_agent()` tool-use slučka + zdieľaný Anthropic klient | runtime |
| `tools/exa_tool.py` | `exa_search` tool schéma + impl (používajú scouti a analyst) | nástroj |
| `scouts.py` | `run_scout(company, beat)` — jeden scout agent na beat; `scout_all()` spustí 4 paralelne | ✔ scout |
| `analyst.py` | `run_analyst(company, signals)` — analyst agent; `apply_guards()` deterministická post-stráž | ✔ analyst |
| `writeback/crm.py` | CRM dispatcher: `upsert_account()` smeruje do Attio alebo HubSpot podľa `CRM_PROVIDER` | — |
| `writeback/outreach.py` | Outreach dispatcher: `queue_lead()` smeruje do Brevo alebo lemlist podľa `OUTREACH_PROVIDER` | — |
| `writeback/{attio,hubspot,slack,brevo,lemlist}.py` | Deterministické, samobrániace API volania | — |
| `router.py` | `run_router(rr, dry_run)` — router agent; write-backy vystavené ako jeho nástroje | ✔ router |
| `reporter.py` + `templates/report.html` | HTML audit log behu | — |
| `main.py` | Orchestruje na účet: scouti → analyst → router → report; `--dry-run` | dirigent |

## Dátový model (kľúčové polia)

- `Signal`: `signal_type`, `title`, `summary`, `source_url`, `published_date`, `relevance`
- `OutreachDraft`: `persona`, `subject`, `first_line`, `body`
- `ICPScore`: `company_name`, `domain`, `score:int`, `tier`, `confidence`, `why_fit`, `why_not`,
  `recommended_persona`, `recommended_angle`, `reasoning`, `needs_human_research:bool`, `outreach`
- `RunResult`: `score`, `signals`, `crm_status`, `slack_status`, `outreach_status`,
  `agent_log: list[str]` (tool-calls, ktoré každý agent spravil — zobrazené v reporte)

## Kde je agentita ohraničená (zámerný human-in-the-loop)

1. **Scouti nesmú vymýšľať.** System prompt zakazuje nezdrojované tvrdenia; analyst nezávisle
   overuje; signály bez dátumu/zdroja sa zahodia.
2. **Skóre analysta stropuje deterministická stráž.** `apply_guards()` — nie model — vynúti
   `confidence=low` + `needs_human_research=true`, keď je dôkazov málo, a zastropuje vysoké skóre
   pri nízkom confidence. Model sa cez to neuhovorí.
3. **Router nikdy neposiela.** `outreach_queue` len pridá lead do **review listu** (Brevo) alebo do
   **pozastavenej** kampane (lemlist); rep skontroluje a pošle. `slack_alert` a `outreach_queue` sa
   samé odmietnu, ak nejde o confident Tier 1 — platí, aj keď router agent rozhodne inak. CRM
   poznámky sú označené „AI-suggested — review before outreach".

## Vrstvené poradie buildu (poistka proti času)

- **T0 (~2,5h, musí bežať):** `agent_core` + `exa_tool` + scouti + analyst + `apply_guards` +
  CRM write-back (Attio cez `crm` dispatcher) + router + report. Kompletný agentický loop do CRM.
  Demo stojí, aj keď nič ďalšie nedobehne.
- **T1 (+30m):** pridať `slack_alert` do sady nástrojov routera.
- **T2 (+45m, najrizikovejší, posledný):** pridať `outreach_queue` (Brevo review list) do sady
  nástrojov routera.

## Cieľový zoznam

8–10 reálnych EU retail/CPG účtov s ozajstnými nedávnymi verejnými triggermi, plus 1–2 zámerne
slabé/malé účty, aby stráž fírla naživo (scouti nájdu málo → analyst flagne → router odmietne
Slack/outreach).

## Ošetrenie chýb

- Každý scout degraduje na prázdno pri akejkoľvek Exa/model chybe; jeden zlý scout nikdy nezhodí účet.
- `run_agent` je ohraničený `max_turns`; agent, ktorý nikdy neskončí, vráti to, čo má.
- Analyst bez použiteľných signálov → konzervatívny `needs_human_research` default.
- Nástroje routera obalené tak, že zlyhaný kanál sa zaloguje do `RunResult` a beh pokračuje.
- `--dry-run`: router agent stále beží a *reálne sa rozhoduje*, ale write-back nástroje simulujú
  (vypíšu, čo by spravili). Bezpečné iterovanie a fallback pre live demo.

## Kde to padá (pre Tomáša)

- Exa šum/zastaranosť pri veľkých brandoch; scout iterácia + analyst overenie tlmia, recall je
  aj tak limitovaný.
- Agentické slučky pridávajú latenciu a varianciu; ohraničené capmi na ťahy, takže najhorší prípad
  je znížený recall, nie zamrznutie. Demo beží 2–3 účty naživo, plný zoznam je pre-run.
- Žiadny reálny e-mail osoby — persona je odporúčaná; demo používa test e-mail ako lead.
- One-shot beh; produkcia = plánovaný beh + diff skóre + alert len pri zmene.
- Žiadny dedup voči existujúcemu CRM pipeline (re-runy vytvárajú nové záznamy; produkcia by
  assert-la/upsert-la).
- CRM je pluggable: postavené na Attio pre okamžité sprovoznenie; HubSpot adaptér je rovnaké
  rozhranie a stane sa cieľom cez `CRM_PROVIDER=hubspot`, keď portál existuje.

## Čo by som staval ďalej (jeden týždeň)

Povýšiť scoutov na MCP-tool agentov (Apollo/LinkedIn/Gong ako nástroje) · discovery agent, ktorý
nájde net-new účty z triggeru pred skórovaním · Gong call-outcome agent zapisujúci späť do HubSpotu ·
reply-handling agent, ktorý vetví lemlist sekvenciu podľa intentu · person-level enrichment pre
reálne e-maily. `run_agent` runtime ostáva; rastú len sady nástrojov.

## Tech

Python 3.11+ · anthropic (tool-use slučka) · exa-py · requests · pydantic · jinja2 · python-dotenv.
Kľúče: `EXA_API_KEY`, `ANTHROPIC_API_KEY`, `CRM_PROVIDER` (attio|hubspot), `ATTIO_API_KEY`,
`HUBSPOT_TOKEN` (voliteľné), `SLACK_WEBHOOK_URL`, `OUTREACH_PROVIDER` (brevo|lemlist),
`BREVO_API_KEY`, `BREVO_LIST_ID`, `LEMLIST_API_KEY` (voliteľné), `LEMLIST_CAMPAIGN_ID` (voliteľné),
`TEST_EMAIL`.
