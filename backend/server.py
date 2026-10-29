"""FINN's research API: pull research on any listed company, with live progress.

    cd backend && .venv/bin/python server.py        # http://localhost:8787

    GET  /api/search?q=polycab       companies matching a name or ticker
    GET  /api/research/POLYCAB       {state, steps, ledger?}   state: idle|queued|running|done|error
    POST /api/research/POLYCAB       start research (no-op if cached or already running)

Standard library only. Jobs run one at a time on a worker thread: the free LLM tier
throttles parallel calls, and a queue is honest about the wait.
"""

import json
import queue
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from finn import research
from finn.config import ROOT
from finn.llm.client import LLMError
from finn.sources import screener

PORT = 8787
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
    with lock:
        jobs[sym] = {"state": "queued", "steps": [], "position": work.qsize() + 1}
    work.put(sym)
    return status(sym)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        try:
            if parts == ["api", "search"] and method == "GET":
                q = (parse_qs(url.query).get("q") or [""])[0].strip()
                if len(q) < 2:
                    return self._send(200, {"results": []})
                results = screener.search(q)
                for r in results:
                    r["ready"] = ledger_for(r["symbol"].upper()) is not None
                return self._send(200, {"results": results})
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
