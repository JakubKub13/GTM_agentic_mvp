# 🧪 Manuálne end-to-end testovanie — duvo-signal-loop

Tento návod popisuje, ako **manuálne** overiť celý pipeline od začiatku do konca: ktoré
príkazy spustiť, ktoré externé služby otvoriť a skontrolovať, ako vyzerá správny výsledok
a približne ako dlho jednotlivé behy trvajú.

> Automatizované testy (`uv run pytest -q`, 281 mockovaných testov) už pokrývajú kód v izolácii.
> Tento dokument je o **živom overení s človekom v slučke** (human-in-the-loop) oproti
> reálnym službám (Exa, Anthropic, Attio, Slack, Brevo).
>
> Tento projekt používa **[uv](https://docs.astral.sh/uv/)**. Príkazy sú uvedené s
> prefixom `uv run` (netreba manuálne aktivovať venv). Ak chceš, môžeš raz spraviť
> `source .venv/bin/activate` a prefix `uv run` vynechať.

---

## 0. Pred začatím (pre-flight)

| Kontrola | Ako |
|---|---|
| uv nainštalované | `uv --version` (inštalácia: `curl -LsSf https://astral.sh/uv/install.sh \| sh`) |
| Prostredie zosynchronizované | `uv sync` (vytvorí `.venv` + nainštaluje všetky závislosti z `uv.lock`) |
| `.env` existuje a je vyplnený | `cp .env.example .env` a uprav |
| Povinné kľúče sú prítomné | `EXA_API_KEY`, `ANTHROPIC_API_KEY` (potrebné aj pre `--dry-run`) |
| CRM pripravené | `CRM_PROVIDER=attio` + `ATTIO_API_KEY` |
| Slack pripravený | `SLACK_WEBHOOK_URL` (incoming webhook pre `#sales`) |
| Outreach pripravený | `OUTREACH_PROVIDER=brevo` + `BREVO_API_KEY` + `BREVO_LIST_ID` |
| Testovacia schránka známa | `TEST_EMAIL` (predvolene `jakubkubala3@gmail.com`) — adresa leadu pre outreach |

Rýchla kontrola, že balík sa importuje a testy prechádzajú:

```bash
uv run python -c "import main; print('import ok')"
uv run pytest -q          # očakávané: 281 passed
```

> ⚠️ Aj `--dry-run` robí **reálne** volania na Exa + Anthropic (simulujú sa len zápisy do
> služieb). Takže `EXA_API_KEY` a `ANTHROPIC_API_KEY` musia byť platné pre akýkoľvek beh.

---

## 1. Ktoré služby otvoriť

Počas testu maj tieto okná/karty otvorené vedľa seba:

| # | Služba | URL | Čo tu overuješ |
|---|---|---|---|
| 1 | 🖥️ **Terminál** | — | Živé logy priebehu, riadky so skóre pre každý account, finálnu cestu `Report:` |
| 2 | 📊 **HTML report** | `output/run-report.html` | Kompletný audit: skóre, tiers, signály, návrh outreachu, volania nástrojov agentmi, stavy zápisov |
| 3 | 🗂️ **Attio** | https://app.attio.com | Vytvorené firemné záznamy + poznámka „AI-suggested“ pre každý account |
| 4 | 💬 **Slack** | tvoj kanál `#sales` | Tier-1 upozornenia (len pre sebavedomé Tier 1 accounty) |
| 5 | ✉️ **Brevo** | https://app.brevo.com | Do review listu pribúdajú nové **kontakty** (Contacts → Lists). **Nič sa neodosiela.** |
| 6 | 📧 **Testovacia schránka** | schránka `TEST_EMAIL` | Over, že outreach e-mail **neprišiel** (garancia „nikdy neodosiela“) |
| 7 | 🔎 **Exa usage** *(voliteľné)* | https://dashboard.exa.ai | Spotreba kreditov za vyhľadávanie (dôkaz, že scouti naozaj hľadali) |
| 8 | 🤖 **Anthropic usage** *(voliteľné)* | https://platform.claude.com | Spotreba tokenov (dôkaz, že agenti naozaj bežali) |

---

## 2. Testovacie scenáre (spúšťaj v tomto poradí)

### ✅ Scenár A — Dry run, malý (najbezpečnejší ako prvý)

Agenti bežia a **naozaj rozhodujú**, ale každý zápis je simulovaný. Nič sa nezapíše do Attia/Slacku/Breva.

```bash
uv run python main.py --dry-run --limit 3
```

**Over:**
- Terminál vypíše riadok `-> <Firma>` a riadok `N/10 <Tier> conf=<...> human=<...>` pre každý account.
- Terminál skončí riadkom `Report: output/run-report.html`.
- Otvor report → každý account ukazuje odznak so skóre, signály s odkazmi na zdroje, návrh úvodnej vety outreachu a zoznam **Agent tool calls** (napr. `exa_search(...)`, `submit_signals(...)`, `record_assessment(...)`, `crm_upsert()`, `finish()`).
- Stavy ukazujú `[dry-run] ...` — pre tento scenár **nekontroluj** Attio/Slack/Brevo (nič sa nezapísalo).

⏱️ **Trvanie:** ~1–2 minúty.

---

### ✅ Scenár B — Dry run, celý zoznam

```bash
uv run python main.py --dry-run
```

**Over:** to isté ako A, ale pre všetkých 10 accountov. Skontroluj, že dva zámerne slabé
accounty (**Tiny Local Bakery**, **Garage Startup XYZ**) vyjdú ako **Tier 3 / nízka istota**
a sú v reporte označené ako **„human research needed“** — to dokazuje, že `apply_guards()`
funguje.

⏱️ **Trvanie:** ~2–4 minúty.

---

### ✅ Scenár C — Reálny beh, malý (zapisuje do živých služieb)

Tento beh naozaj zapisuje do Attia (a pri sebavedomom Tier 1 aj do Slacku + Breva).

```bash
uv run python main.py --limit 2
```

**Over v jednotlivých službách:**

| Služba | Očakávané |
|---|---|
| 🗂️ Attio | **Company** záznam pre každý spracovaný account + dôkazová **Note** s názvom `ICP N/10 (...) — AI-suggested, review before outreach`. Telo poznámky obsahuje „AI-SUGGESTED“, skóre, why-fit / why-not, zdôvodnenie a návrh úvodnej vety. |
| 💬 Slack `#sales` | **Tier-1 upozornenie** *len ak* je niektorý z 2 accountov sebavedomé Tier 1 (hlavička `🟢 Tier 1: <Firma> (N/10)`, persona, istota, uhol, návrh vety). Ak ani jeden nie je sebavedomé Tier 1, **žiadna** Slack správa — to je správne. |
| ✉️ Brevo | Nový **kontakt** v review liste (`BREVO_LIST_ID`) len pre sebavedomé Tier 1. Otvor Contacts → Lists → tvoj list. |
| 📧 Testovacia schránka | **Žiadny outreach e-mail neprišiel.** Lead len čaká v review liste na človeka. |
| 📊 Report | Stavy teraz ukazujú reálne hodnoty (napr. `attio company <uuid> ...`, `alert posted to #sales`, `contact queued in Brevo review list ...`). |

⏱️ **Trvanie:** ~1–2 minúty.

---

### ✅ Scenár D — Plný reálny beh

```bash
uv run python main.py
```

**Over:** spracuje sa všetkých 10 accountov; sebavedomé Tier-1 accounty spustia `crm_upsert`
**+** `slack_alert` **+** `outreach_queue`; slabé / non-Tier-1 accounty dostanú **len**
`crm_upsert` (Slack/outreach sa samé odmietnu — vidno v reporte v logu volaní nástrojov ako
odmietnutie „refused“).

⏱️ **Trvanie:** ~3–6 minút (10 accountov beží súbežne, predvolene 5 naraz).

---

### ✅ Scenár E — Verbose / debugovanie

```bash
uv run python main.py --dry-run --limit 1 --log-level DEBUG
```

**Over:** terminál ukáže každý ťah (turn) agenta, každé volanie nástroja aj s argumentmi a
každé rozhodnutie guardu. Užitočné na sledovanie jedného accountu cez scout → analyst → router
do detailu.

⏱️ **Trvanie:** ~30–60 sekúnd.

---

## 3. Kontrolný zoznam bezpečnosti / human-in-the-loop

Toto sú garancie, ktoré výslovne over počas **reálneho** behu (Scenár C/D):

- [ ] **Nikdy neodosiela:** do schránky `TEST_EMAIL` nepríde žiadny e-mail — leady sa objavia len v Brevo review liste.
- [ ] **Strážené routovanie:** non-sebavedomé-Tier-1 accounty dostanú iba CRM záznam (žiadny Slack, žiadny Brevo kontakt).
- [ ] **Guard limituje istotu:** dva slabé demo accounty sú Tier 3 + „human research needed“.
- [ ] **Izolácia zlyhania:** ak jeden account zlyhá (napr. dočasný výpadok API), beh pokračuje a report sa aj tak vygeneruje pre zvyšok. Tlak vieš odsimulovať malým timeoutom: `ACCOUNT_TIMEOUT_SECONDS=0.01 uv run python main.py --limit 3` → accounty vypršia, zalogujú sa ako zlyhané a report sa aj tak vykreslí (s 0 výsledkami) bez pádu procesu.
- [ ] **Žiadne tajomstvá v logoch:** prejdi výstup terminálu — API kľúče ani Slack webhook URL sa nikdy neobjavia.

---

## 4. Ladiace prepínače (voliteľné počas testovania)

| Prepínač / env | Účinok |
|---|---|
| `--limit N` | Spracuje len prvých N accountov (rýchlejšie) |
| `--concurrency N` | Prepíše `MAX_CONCURRENT_ACCOUNTS` (predvolene 5) — zníž pri rate limitoch |
| `--log-level DEBUG` | Plný trace ťahov, volaní nástrojov a rozhodnutí guardu |
| `--test-email you@example.com` | Prepíše adresu leadu pre outreach |
| `MAX_CONCURRENT_ACCOUNTS` | Strop súbežnosti na úrovni accountov |
| `ACCOUNT_TIMEOUT_SECONDS` | Strop reálneho času na account (predvolene 300) |

---

## 5. Očakávané trvania v skratke

| Príkaz | Accounty | Približný reálny čas |
|---|---|---|
| `--dry-run --limit 3` | 3 | ~1–2 min |
| `--dry-run` | 10 | ~2–4 min |
| `--limit 2` (reálny) | 2 | ~1–2 min |
| *(plný)* `uv run python main.py` | 10 | ~3–6 min |
| `--limit 1 --log-level DEBUG` | 1 | ~30–60 s |

> Časy sa líšia podľa latencie Exa/Anthropic, počtu vyhľadávaní, ktoré si agent zvolí, a podľa
> nastavenia súbežnosti. Na account je práca ohraničená cez `ACCOUNT_TIMEOUT_SECONDS` (5 min),
> na jedno volanie modelu cez `ANTHROPIC_TIMEOUT_SECONDS` (120 s) a na jeden HTTP zápis cez
> `HTTP_TIMEOUT_SECONDS` (30 s).

---

## 6. Upratovanie (voliteľné)

- Vygenerované reporty sú v `output/` (gitignored) — pokojne zmaž.
- V **Attio** zmaž testovacie firemné záznamy/poznámky, ak ich nechceš ponechať
  (zatiaľ nie je automatická deduplikácia — opakované behy vytvárajú nové záznamy).
- V **Brevo** odstráň testovací kontakt(y) z review listu, ak chceš.

---

## ✅ Definícia hotového (Definition of done)

Beh je úspešne overený, keď:
1. Terminál skončí bez neošetrenej výnimky a vypíše cestu k reportu.
2. `output/run-report.html` sa otvorí a ukazuje správne skóre, signály, návrhy a stavy.
3. (Reálny beh) Attio ukazuje firmy + poznámky „AI-suggested“; sebavedomé Tier-1 sa objavia v Slacku a v Brevo review liste.
4. Žiadny outreach e-mail nebol reálne doručený.
5. Dva slabé demo accounty sú správne zoradené nižšie a označené.
