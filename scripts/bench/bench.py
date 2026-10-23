#!/usr/bin/env python3
"""Agent Zero performance harness (turns, HTTP, UI).

Usage:
  python scripts/bench/bench.py http [--port 5010] [--n 200]
  python scripts/bench/bench.py turns [--port 5010] [--reps 5] [--prompt TEXT]
  python scripts/bench/bench.py ui [--url http://127.0.0.1:5010/] [--mode cold|warm] [--reps 3]

It only talks to a running A0 instance; start one from an isolated data dir first.
Requires the venv (httpx, patchright) and a Chromium build under tmp/playwright.
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
import time

import httpx


def _client(port: int) -> httpx.Client:
    base = f"http://127.0.0.1:{port}"
    c = httpx.Client(base_url=base, headers={"Origin": base}, timeout=180)
    token = c.get("/api/csrf_token").json()["token"]
    c.headers["X-CSRF-Token"] = token
    return c


def bench_http(port: int, n: int) -> dict:
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)
    lat = []
    t0 = time.perf_counter()
    for _ in range(n):
        t = time.perf_counter()
        c.get("/api/health").read()
        lat.append(time.perf_counter() - t)
    total = time.perf_counter() - t0
    return {
        "path": "/api/health",
        "requests": n,
        "total_s": round(total, 3),
        "req_per_s": round(n / total, 1),
        "median_ms": round(statistics.median(lat) * 1000, 2),
    }


def bench_turns(port: int, reps: int, prompt: str) -> dict:
    c = _client(port)
    times = []
    replies = []
    for _ in range(reps):
        ctx = c.post("/api/chat_create", json={}).json()["ctxid"]
        t0 = time.perf_counter()
        out = c.post("/api/message", json={"text": prompt, "context": ctx}).json()
        times.append(time.perf_counter() - t0)
        replies.append(str(out.get("message", ""))[:120])
    return {
        "prompt": prompt,
        "reps": reps,
        "runs_s": [round(x, 2) for x in times],
        "median_s": round(statistics.median(times), 2),
        "min_s": round(min(times), 2),
        "max_s": round(max(times), 2),
        "replies": replies,
    }


JS_METRICS = """() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paints = Object.fromEntries(performance.getEntriesByType('paint').map(p => [p.name, p.startTime]));
  const res = performance.getEntriesByType('resource');
  let transfer = 0, encoded = 0;
  for (const r of res) { transfer += r.transferSize || 0; encoded += r.encodedBodySize || 0; }
  return { ttfb: nav.responseStart, dcl: nav.domContentLoadedEventEnd, load: nav.loadEventEnd,
           fcp: paints['first-contentful-paint'] || null, requests: res.length,
           transfer_kb: Math.round(transfer/1024), encoded_kb: Math.round(encoded/1024) };
}"""


def bench_ui(url: str, mode: str, reps: int) -> dict:
    from patchright.sync_api import sync_playwright

    exe = (glob.glob("tmp/playwright/chromium-*/chrome-linux64/chrome") or [None])[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        if mode == "cold":
            page.goto(url, wait_until="domcontentloaded")
            page.evaluate("""async () => {
              if (window.caches) { for (const k of await caches.keys()) await caches.delete(k); }
              if (navigator.serviceWorker) { const rs = await navigator.serviceWorker.getRegistrations(); await Promise.all(rs.map(r => r.unregister())); }
            }""")
            ctx.clear_cookies()
        rows = []
        for _ in range(reps):
            t0 = time.perf_counter()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("#chat-input", state="visible", timeout=60000)
            ready = (time.perf_counter() - t0) * 1000
            row = page.evaluate(JS_METRICS)
            row["app_ready_ms"] = round(ready, 1)
            rows.append(row)
        med = {}
        for key in rows[0]:
            vals = [r[key] for r in rows if r[key] is not None]
            med[key] = round(statistics.median(vals), 1) if vals else None
        browser.close()
    return {"url": url, "mode": mode, "runs": rows, "median": med}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("http", "turns", "ui"))
    ap.add_argument("--port", type=int, default=5010)
    ap.add_argument("--url", default="http://127.0.0.1:5010/")
    ap.add_argument("--mode", choices=("cold", "warm"), default="warm")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--prompt", default="Reply with exactly: PONG")
    args = ap.parse_args()

    if args.command == "http":
        result = bench_http(args.port, args.n)
    elif args.command == "turns":
        result = bench_turns(args.port, args.reps, args.prompt)
    else:
        result = bench_ui(args.url, args.mode, args.reps)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
