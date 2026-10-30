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

## Daily refresh

`.github/workflows/refresh.yml` fetches end-of-day prices every evening at 19:00 IST (and on demand from the Actions tab) and commits them if they changed, so price and volume alerts stay current. It needs no secrets.

Filings are refreshed by hand: BSE's announcements API answers 403 to GitHub's runners, so they have to be fetched from a machine in India. About a minute, since documents and model answers are cached:

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
