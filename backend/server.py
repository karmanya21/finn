"""FINN's research API: pull research on any listed company, with live progress.

    cd backend && .venv/bin/python server.py        # http://localhost:8787

    GET  /api/health                 {ok: true}, for the host's health check
    GET  /api/search?q=polycab       companies matching a name or ticker
    GET  /api/research/POLYCAB       {state, steps, ledger?}   state: idle|queued|running|done|error
    POST /api/research/POLYCAB       start research (no-op if cached or already running)
    POST /api/ask/POLYCAB            {question} -> an answer from that company's record only, each sentence cited
    POST /api/ask-portfolio          {question, symbols} -> the same, across the reader's whole portfolio

Standard library only. Jobs run one at a time on a worker thread: the free LLM tier
throttles parallel calls, and a queue is honest about the wait.
"""

import json
import os
import queue
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from finn import ask, research
from finn.config import ROOT
from finn.llm.client import LLMError
from finn.sources import screener

PORT = int(os.getenv("PORT", "8787"))  # a host (Render) sets PORT
ALLOWED_ORIGIN = os.getenv("FINN_ALLOWED_ORIGIN", "*")  # the web app's origin, when it's hosted apart

# A public link spends the free model allowance, so cap it: questions per visitor per hour,
# and new companies researched per day (a research run is dozens of model calls).
ASK_PER_HOUR = int(os.getenv("FINN_ASK_PER_HOUR", "30"))
RESEARCH_PER_DAY = int(os.getenv("FINN_RESEARCH_PER_DAY", "8"))
_asks: dict[str, list[float]] = {}
_research_days: dict[str, int] = {}


def _allow_ask(who: str) -> bool:
    now = time.time()
    with lock:
        recent = [t for t in _asks.get(who, []) if now - t < 3600]
        if len(recent) >= ASK_PER_HOUR:
            _asks[who] = recent
            return False
        _asks[who] = recent + [now]
        return True


def _allow_research() -> bool:
    day = time.strftime("%Y-%m-%d")
    with lock:
        n = _research_days.get(day, 0)
        if n >= RESEARCH_PER_DAY:
            return False
        _research_days.clear()  # only today's count matters
        _research_days[day] = n + 1
        return True
PORTFOLIO_DATA = ROOT / "web" / "public" / "data"
SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")

jobs: dict[str, dict] = {}
lock = threading.Lock()
work: "queue.Queue[str]" = queue.Queue()


def ledger_for(sym: str) -> dict | None:
    p = PORTFOLIO_DATA / f"{sym}.json"
    return json.loads(p.read_text()) if p.exists() else research.cached(sym)


def worker() -> None:
    while True:
        sym = work.get()
        job = jobs[sym]

        def progress(step: str, detail: str) -> None:
            with lock:
                job["state"] = "running"
                job["steps"].append({"step": step, "detail": detail, "at": time.time()})

        try:
            research.research(sym, progress)
            with lock:
                job["state"] = "done"
        except requests.HTTPError as e:
            with lock:
                job.update(state="error", error=(f"Couldn't find {sym} on screener.in."
                                                 if e.response is not None and e.response.status_code == 404
                                                 else f"A data source failed: {e}"))
        except LLMError as e:
            with lock:
                job.update(state="error", error=f"The language model is unavailable right now ({e}). Try again shortly.")
        except Exception as e:  # keep the worker alive; report the failure
            traceback.print_exc()
            with lock:
                job.update(state="error", error=f"Research failed: {e}")
        finally:
            work.task_done()


def status(sym: str) -> dict:
    with lock:
        job = dict(jobs.get(sym, {}))
    if job.get("state") in ("queued", "running", "error"):
        return {"symbol": sym, **job}
    ledger = ledger_for(sym)
    if ledger:
        return {"symbol": sym, "state": "done", "steps": job.get("steps", []), "ledger": ledger}
    return {"symbol": sym, "state": "idle", "steps": []}


def start(sym: str) -> dict:
    with lock:
        if jobs.get(sym, {}).get("state") in ("queued", "running"):
            return {"symbol": sym, **jobs[sym]}
    if ledger_for(sym):
        return status(sym)
    if not _allow_research():
        return {"symbol": sym, "state": "error", "steps": [],
                "error": f"This demo researches up to {RESEARCH_PER_DAY} new companies a day on a free model tier, "
                         "and today's are used. Companies already researched still open instantly."}
    with lock:
        jobs[sym] = {"state": "queued", "steps": [], "position": work.qsize() + 1}
    work.put(sym)
    return status(sym)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self):  # the browser's preflight before a cross-origin POST
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _visitor(self) -> str:
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        try:
            if parts == ["api", "health"]:
                return self._send(200, {"ok": True})
            if parts == ["api", "search"] and method == "GET":
                q = (parse_qs(url.query).get("q") or [""])[0].strip()
                if len(q) < 2:
                    return self._send(200, {"results": []})
                results = screener.search(q)
                for r in results:
                    r["ready"] = ledger_for(r["symbol"].upper()) is not None
                return self._send(200, {"results": results})
            if len(parts) == 3 and parts[:2] == ["api", "ask"] and method == "POST":
                sym = parts[2].upper()
                if not SYMBOL.match(sym):
                    return self._send(400, {"error": "Not a ticker symbol"})
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                question = str(body.get("question") or "").strip()[:300]
                if len(question) < 3:
                    return self._send(400, {"error": "Ask a question"})
                ledger = ledger_for(sym)
                if not ledger:
                    return self._send(404, {"error": f"No record for {sym} yet"})
                if not _allow_ask(self._visitor()):
                    return self._send(429, {"error": f"That's {ASK_PER_HOUR} questions this hour, the most this free demo allows. Try again later."})
                try:
                    history = body.get("history") if isinstance(body.get("history"), list) else []
                    return self._send(200, ask.ask(sym, question, ledger, history))
                except LLMError as e:
                    return self._send(503, {"error": f"The language model is unavailable right now ({e}). Try again shortly."})
            if parts == ["api", "ask-portfolio"] and method == "POST":
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                question = str(body.get("question") or "").strip()[:300]
                if len(question) < 3:
                    return self._send(400, {"error": "Ask a question"})
                syms = [s.upper() for s in body.get("symbols") or [] if isinstance(s, str) and SYMBOL.match(s.upper())][:40]
                ledgers = {s: l for s in dict.fromkeys(syms) if (l := ledger_for(s))}
                if not ledgers:
                    return self._send(400, {"error": "No companies to answer about"})
                if not _allow_ask(self._visitor()):
                    return self._send(429, {"error": f"That's {ASK_PER_HOUR} questions this hour, the most this free demo allows. Try again later."})
                try:
                    history = body.get("history") if isinstance(body.get("history"), list) else []
                    out = ask.ask_portfolio(question, ledgers, history)
                    return self._send(200, out | {"missing": [s for s in syms if s not in ledgers]})
                except LLMError as e:
                    return self._send(503, {"error": f"The language model is unavailable right now ({e}). Try again shortly."})
            if len(parts) == 3 and parts[:2] == ["api", "research"]:
                sym = parts[2].upper()
                if not SYMBOL.match(sym):
                    return self._send(400, {"error": "Not a ticker symbol"})
                return self._send(200, start(sym) if method == "POST" else status(sym))
            self._send(404, {"error": "Not found"})
        except requests.RequestException as e:
            self._send(502, {"error": f"screener.in didn't respond: {e}"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def log_message(self, fmt, *args):  # quieter console: only errors and research steps
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    print(f"FINN research API on http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
