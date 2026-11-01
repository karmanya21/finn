# FINN

FINN remembers what management promised, watches whether they deliver, and stays quiet until something matters.

It reads earnings-call transcripts, pulls out management's forward-looking promises (growth, margins, capex, milestones), follows each one across later calls, and checks it against reported results: **kept, missed, moved, gone quiet**, or honestly **can't verify**.

## Run it

```bash
# 1. Python pipeline (needs a free Gemini key in .env; see .env.example)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.fetch_transcripts          # transcripts for the spike companies
.venv/bin/python -m scripts.extract_all                # promises per call (cheap model, cached)
.venv/bin/python -m scripts.build_ledger               # threads + verdicts -> web/public/data/

# 2. Research API (pull: any NSE company, live)
.venv/bin/python server.py                             # http://localhost:8787

# 3. App (in another terminal)
cd ../web && npm install && npm run dev                # http://localhost:5173
```

**Push** is the home feed: what moved across the portfolio's promise records. **Pull** is the search bar: type any listed company and FINN builds its promise record live from its own transcripts and reported results (a minute or two the first time, cached after). Companies with no earnings calls get their headline numbers and an honest "no promise record".

Quality check: `.venv/bin/python -m scripts.eval_extraction` scores extraction against the hand-labelled set in `eval/gold/`.

## Public prototype

`render.yaml` hosts both halves on Render's free plan: the app as a static site (always on) and the research/Ask API as a web service (it sleeps when idle, so the first question after a quiet spell takes up to a minute). Set up once from the Render dashboard: **New → Blueprint →** this repo, and paste `GEMINI_API_KEY` when asked. Each push to `main` redeploys, so the daily refreshes reach the link. The API caps use of the free model: 30 questions per visitor an hour and 8 new companies researched a day (`FINN_ASK_PER_HOUR`, `FINN_RESEARCH_PER_DAY`).

## Tests and audit

```bash
cd backend && .venv/bin/python -m unittest discover -s tests -t .   # regression tests (no network)
cd backend && .venv/bin/python -m scripts.audit                     # invariant checks on all built output
cd web && npm test                                                   # formatting tests
```

The regression tests pin every wrong output found by hand (fake cuts from numbers that weren't in a quote, "twice" read as 2%, an evidence figure lifted from the wrong sentence, a standalone statement read as consolidated, misfiled USFDA and NCD filings, ...). The audit checks the *classes* of error on real output, so companies nobody has reviewed still get checked; every build ends with it, and `--strict` makes errors fail the build. The tests run on every push (`.github/workflows/tests.yml`).

## Daily refresh

`.github/workflows/refresh.yml` fetches end-of-day prices every evening at 19:00 IST (and on demand from the Actions tab) and commits them if they changed, so price and volume alerts stay current. It needs no secrets.

Filings can't be fetched from GitHub: BSE's announcements API answers 403 to its runners. They refresh daily from a free server in India instead; [`deploy/india-refresh/`](deploy/india-refresh/README.md) sets it up in two commands. Or refresh by hand from any machine in India (about a minute, since documents and model answers are cached):

```bash
cd backend && .venv/bin/python -m scripts.build_push filings
git add web/public/data/filings.json && git commit -m "Refresh filings" && git push
```

If most companies fail to fetch, the script keeps the previous digest rather than writing an empty one. Promise records change only when a new earnings call lands, so they're rebuilt by hand with `scripts.build_all`.

## How it works

| Stage | What | Model |
|---|---|---|
| Fetch | screener.in pages, BSE transcript PDFs → text | none |
| Pre-filter | keep management sentences with a forward cue *and* something checkable (−81% tokens) | none |
| Extract | structured promises; every quote must appear verbatim in the source or it's rejected | cheap, 2 passes |
| Link | group statements across calls into one thread per commitment | strong, falls back to cheap |
| Evidence | for promises financials can't settle, retrieve management's later sentences and classify | cheap |
| Resolve | raised/cut/deferred, verdicts, H2 pace: arithmetic on reported numbers | none (code) |

Verdicts say where they come from: **reported financials** (screener consolidated) or **per management** (their own later words, not independently verified). Constant-currency guidance is never checked against rupee numbers.

Every model response is cached by prompt hash, so re-runs are free.

**Free-tier budget.** Gemini's free allowance is counted per model per day (in Oct 2026: 500 requests for 3.5 Flash-Lite, 20 for 3.5 Flash). The client walks a chain (preferred model → fallback → cheap pool), skips a model for 10 minutes when it's overloaded or hanging, and skips it until Google's reset when its daily allowance is used up (remembered in `data/cache/exhausted.json`, shared by the pipeline and the API server). Building all 22 companies plus a 30-day filings digest takes roughly 600 requests, so it spans two Flash-Lite models.
