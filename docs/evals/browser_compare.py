"""Bounded comparison: plain HTTP vs Playwright Chromium vs Camoufox, on
Hunter's own company-description extraction (companyintel.site_facts).

An evaluation tool, not part of hunter: nothing imports it and the scheduled
runs never call it. The record it produced is tests/fixtures/browser_eval.json,
pinned by tests/test_browser_eval.py, and docs/evals/BROWSER_EVAL.md says what
it found. To run it again, outside the repo:

  1. python -m venv venv && venv/bin/pip install playwright psutil requests
     google-auth anthropic openai pypdf  (Chromium: set HUNTER_CHROMIUM_PATH)
  2. git clone https://github.com/jo-inc/camofox-browser, then npm ci in it.
     Its installer fetches the browser via api.github.com; where that is
     blocked, download the pinned release zip and set CAMOUFOX_INSTALL_DIR.
  3. Start it local only, telemetry off, behind a key written to
     $BROWSER_EVAL_DIR/camofox.key:
       CAMOFOX_CRASH_REPORT_ENABLED=false CAMOFOX_BIND_HOST=127.0.0.1 \
       CAMOFOX_ACCESS_KEY=$(cat $BROWSER_EVAL_DIR/camofox.key) node server.js
  4. for a in plain chromium camoufox; do
       venv/bin/python docs/evals/browser_compare.py $a 2; done
     venv/bin/python docs/evals/browser_summarise.py tests/fixtures/browser_eval.json

The extraction is Hunter's, unchanged: each adapter only supplies the page
HTML through the one seam site_facts already uses (companyintel._fetch). So the
schema, the prose test and the about-page fallback are identical for all three.

Rules this harness keeps:
  - robots.txt is read first; a host that disallows "/" for "*" is skipped by
    every adapter.
  - a page that answers 401/403/407/429, or presents a bot challenge, is
    recorded as ACCESS RESTRICTED and nothing is extracted from it.
  - one adapter at a time over the whole list (adapter-major order) with a
    pause between requests, so no host sees a burst.
  - Camoufox runs with its crash telemetry off, bound to 127.0.0.1, behind an
    access key.

Pass bar, declared before the run (see the report): Camoufox is adopted only if
on the gap set, excluding restricted targets, it reads a description on at
least 3 more targets AND 10 points more than Chromium, or equal success at 30
percent less median time and 30 percent less peak memory.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit

import psutil
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
from hunter import companyintel  # noqa: E402

# Working folder: results/ is written here, and camofox.key (the access key the
# local server was started with) is read from here. Never inside the repo.
S = os.path.abspath(os.environ.get("BROWSER_EVAL_DIR", "browser-eval"))
OUT = os.path.join(S, "results")
os.makedirs(OUT, exist_ok=True)

# Gap: his named companies whose description Hunter did NOT take from their own
# homepage (a VC portfolio page, a posting, or nothing). Control: ones it did.
GAP = {
    "OpenAI": "https://openai.com/", "Anthropic": "https://www.anthropic.com/",
    "Canva": "https://www.canva.com/", "Cognition": "https://cognition.ai/",
    "Databricks": "https://www.databricks.com/", "Decagon": "https://decagon.ai/",
    "Figma": "https://www.figma.com/", "Gamma": "https://gamma.app/",
    "Glean": "https://www.glean.com/", "HeyGen": "https://www.heygen.com/",
    "Isomorphic Labs": "https://www.isomorphiclabs.com/",
    "Luma AI": "https://lumalabs.ai/", "Mistral AI": "https://mistral.ai/",
    "Modal": "https://modal.com/", "n8n": "https://n8n.io/",
    "Perplexity": "https://www.perplexity.ai/", "Scale AI": "https://scale.com/",
    "Sierra": "https://sierra.ai/", "Sourcegraph": "https://sourcegraph.com/",
    "Supabase": "https://supabase.com/", "Synthesia": "https://www.synthesia.io/",
    "Thinking Machines Lab": "https://thinkingmachines.ai/",
    "Together AI": "https://www.together.ai/", "Unify": "https://www.unifygtm.com/",
    "Vercel": "https://vercel.com/", "Hebbia": "https://www.hebbia.com/",
    "Midjourney": "https://www.midjourney.com/", "Reflection AI": "https://reflection.ai/",
    "Tempus AI": "https://www.tempus.com/", "Magic": "https://magic.dev/",
    "Artisan": "https://www.artisan.co/",
}
CONTROL = {
    "Abridge": "https://www.abridge.com/", "Baseten": "https://www.baseten.co/",
    "Braintrust": "https://www.braintrust.dev/", "Clay": "https://www.clay.com/",
    "Cohere": "https://cohere.com/", "Crusoe": "https://www.crusoe.ai/",
    "Descript": "https://www.descript.com/", "ElevenLabs": "https://elevenlabs.io/",
    "Fireworks AI": "https://fireworks.ai/", "Groq": "https://groq.com/",
    "Harvey": "https://www.harvey.ai/", "Writer": "https://writer.com/",
}

PAUSE = 1.0
CHALLENGE_TITLE = re.compile(
    r"<title[^>]*>\s*(just a moment|attention required|access denied|"
    r"are you a robot|verify you are human|security check|pardon our interruption|"
    r"request unsuccessful|403 forbidden|blocked)", re.I)
CHALLENGE_BODY = re.compile(
    r"cf-chl-|cf_chl_opt|/cdn-cgi/challenge-platform/h/[bg]/orchestrate|"
    r"px-captcha|_Incapsula_Resource|geo\.captcha-delivery\.com|"
    r"g-recaptcha|h-captcha|challenges\.cloudflare\.com/turnstile", re.I)


def restricted(status: int | None, html: str, headers: dict | None = None) -> str:
    """Why this response is an access restriction, or "" when it is not."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    if status in (401, 403, 407, 429):
        return f"http {status}"
    if h.get("cf-mitigated"):
        return "cf-mitigated header"
    if CHALLENGE_TITLE.search(html[:4000] or ""):
        return "challenge title"
    if status and status >= 400 and CHALLENGE_BODY.search(html or ""):
        return "challenge page"
    if CHALLENGE_BODY.search(html or "") and len(companyintel._clean(html)) < 400:
        return "challenge page"
    return ""


ROBOTS: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str) -> bool:
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}"
    if host not in ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(host + "/robots.txt", timeout=10,
                             headers=companyintel.UA)
            if r.status_code >= 400:
                rp = None  # no robots file: nothing disallowed
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        ROBOTS[host] = rp
    rp = ROBOTS[host]
    return True if rp is None else rp.can_fetch("*", url)


class Recorder:
    """Every page load an adapter made, with time and status."""

    def __init__(self):
        self.loads: list[dict] = []

    def add(self, **kw):
        self.loads.append(kw)


# ---------------------------------------------------------------- adapters
class Plain:
    name = "plain"

    def __init__(self, rec):
        self.rec = rec

    def start(self):
        pass

    def stop(self):
        pass

    def tree(self):
        return []

    def fetch(self, url, timeout):
        t = time.monotonic()
        # Hunter's own two-step read: polite UA, then the browser UA string.
        status, html, final, why, hdrs = None, "", "", "", {}
        for headers in (companyintel.UA, companyintel.BROWSER_UA):
            try:
                r = requests.get(url, headers=headers, timeout=timeout,
                                 allow_redirects=True)
            except Exception as e:
                why = f"fetch failed: {e.__class__.__name__}"
                break
            status, hdrs = r.status_code, dict(r.headers)
            if r.status_code < 400:
                html, final = r.text, str(r.url)
                break
            why = f"site answered {r.status_code}"
            html = r.text
        return self._finish(url, t, status, html, final, why, hdrs)

    def _finish(self, url, t, status, html, final, why, hdrs):
        ms = int((time.monotonic() - t) * 1000)
        rs = restricted(status, html, hdrs)
        self.rec.add(adapter=self.name, url=url, ms=ms, status=status,
                     restricted=rs, error=why if not html else "",
                     chars=len(html))
        if rs:
            return "", "", f"ACCESS RESTRICTED ({rs})"
        if status and status >= 400:
            # Hunter's own _fetch gives nothing back for an error page.
            return "", "", why or f"site answered {status}"
        if not html:
            return "", "", why or "empty"
        return html, final, ""


class Chromium(Plain):
    """Hunter's existing browser stack: Playwright Chromium, launched the
    way apply/submit.py launches it (headless, executable path given)."""
    name = "chromium"

    def start(self):
        from playwright.sync_api import sync_playwright
        t = time.monotonic()
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=True,
            executable_path=os.environ.get("HUNTER_CHROMIUM_PATH") or None)
        self.cold_ms = int((time.monotonic() - t) * 1000)

    def stop(self):
        self.browser.close()
        self._pw.stop()

    def tree(self):
        me = psutil.Process()
        return [p for p in me.children(recursive=True)]

    def fetch(self, url, timeout):
        t = time.monotonic()
        ctx = self.browser.new_context()
        page = ctx.new_page()
        status, html, final, why, hdrs = None, "", "", "", {}
        try:
            resp = page.goto(url, wait_until="domcontentloaded",
                             timeout=timeout * 1000)
            if resp is not None:
                status, hdrs = resp.status, resp.headers
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            html, final = page.content(), page.url
        except Exception as e:
            why = f"render failed: {e.__class__.__name__}: {str(e)[:80]}"
        finally:
            ctx.close()
        if status and status >= 400 and not restricted(status, html, hdrs):
            why, html = f"site answered {status}", html
        return self._finish(url, t, status, html, final, why, hdrs)


class Camoufox(Plain):
    """The candidate: jo-inc camofox-browser REST server, local only."""
    name = "camoufox"
    BASE = "http://127.0.0.1:9377"

    def __init__(self, rec):
        super().__init__(rec)
        key = open(os.path.join(S, "camofox.key")).read().strip()
        self.h = {"Authorization": f"Bearer {key}"}
        self.n = 0

    def start(self):
        t = time.monotonic()
        requests.post(self.BASE + "/start", headers=self.h, timeout=60)
        self.cold_ms = int((time.monotonic() - t) * 1000)

    def tree(self):
        out = []
        for p in psutil.process_iter(["cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if "node server.js" in cmd:
                out.append(p)
                out += p.children(recursive=True)
        return out

    def fetch(self, url, timeout):
        t = time.monotonic()
        self.n += 1
        status, html, final, why = None, "", "", ""
        tab = None
        try:
            r = requests.post(self.BASE + "/tabs", headers=self.h, timeout=timeout + 20,
                              json={"userId": "eval", "sessionKey": f"s{self.n}", "url": url})
            body = r.json()
            tab = body.get("tabId")
            status = body.get("httpStatus")
            if not tab or r.status_code >= 400:
                why = f"render failed: {body.get('code') or body.get('error')}"[:120]
            else:
                requests.post(f"{self.BASE}/tabs/{tab}/wait", headers=self.h, timeout=20,
                              json={"userId": "eval", "timeout": 5000, "waitForNetwork": True})
                e = requests.post(f"{self.BASE}/tabs/{tab}/evaluate", headers=self.h, timeout=30,
                                  json={"userId": "eval",
                                        "expression": "[document.documentElement.outerHTML, location.href]"})
                res = e.json().get("result") or ["", ""]
                html, final = res[0] or "", res[1] or url
        except Exception as ex:
            why = f"render failed: {ex.__class__.__name__}"
        finally:
            if tab:
                try:
                    requests.delete(f"{self.BASE}/tabs/{tab}", headers=self.h, timeout=10,
                                    params={"userId": "eval"})
                except Exception:
                    pass
        if status and status >= 400 and not restricted(status, html):
            why = f"site answered {status}"
        return self._finish(url, t, status, html, final, why, {})


# ---------------------------------------------------------------- sampler
class Sampler(threading.Thread):
    def __init__(self, adapter):
        super().__init__(daemon=True)
        self.a, self.stop_ev = adapter, threading.Event()
        self.peak_mb, self.samples, self.cpu = 0.0, [], {}

    def run(self):
        while not self.stop_ev.is_set():
            total = 0.0
            for p in self.a.tree():
                try:
                    total += p.memory_info().rss / 2**20
                    ct = p.cpu_times()
                    self.cpu[p.pid] = ct.user + ct.system
                except psutil.Error:
                    pass
            self.samples.append(total)
            self.peak_mb = max(self.peak_mb, total)
            time.sleep(0.25)


def run(adapter_cls, passes: int, targets: dict):
    rec = Recorder()
    a = adapter_cls(rec)
    a.start()
    base_cpu = {}
    for p in a.tree():
        try:
            ct = p.cpu_times(); base_cpu[p.pid] = ct.user + ct.system
        except psutil.Error:
            pass
    sm = Sampler(a); sm.start()
    results = []
    t_all = time.monotonic()
    for ps in range(passes):
        for name, url in targets.items():
            group = "gap" if name in GAP else "control"
            if not robots_allows(url):
                results.append(dict(company=name, group=group, url=url, pass_=ps,
                                    outcome="robots disallow", desc="", source="", ms=0))
                continue
            start_n = len(rec.loads)
            orig = companyintel._fetch
            companyintel._fetch = lambda u, timeout: (time.sleep(PAUSE), a.fetch(u, timeout))[1]
            t = time.monotonic()
            try:
                facts, notes = companyintel.site_facts(url, timeout=25)
            except Exception as ex:  # recorded, never allowed to end the run
                facts, notes = {}, [f"extraction raised {ex.__class__.__name__}"]
            finally:
                companyintel._fetch = orig
            ms = int((time.monotonic() - t) * 1000) - PAUSE * 1000 * (len(rec.loads) - start_n)
            loads = rec.loads[start_n:]
            rs = [l["restricted"] for l in loads if l["restricted"]]
            fact = facts.get("what_it_does")
            if fact:
                outcome = "described"
            elif rs:
                outcome = "access restricted"
            elif all(l["error"] for l in loads):
                outcome = "error"
            else:
                outcome = "no description"
            results.append(dict(company=name, group=group, url=url, pass_=ps,
                                outcome=outcome, restricted=rs, notes=notes,
                                desc=(fact.value[:160] if fact else ""),
                                source=(fact.source if fact else ""),
                                ms=int(ms), loads=len(loads),
                                statuses=[l["status"] for l in loads]))
            print(f"[{a.name} p{ps}] {name:22s} {outcome:18s} {int(ms):6d}ms "
                  f"{(fact.value[:60] if fact else '; '.join(rs or notes))[:70]}", flush=True)
    wall = time.monotonic() - t_all
    sm.stop_ev.set(); sm.join()
    cpu = sum(max(0.0, v - base_cpu.get(pid, 0.0)) for pid, v in sm.cpu.items())
    a.stop()
    summary = dict(adapter=a.name, wall_s=round(wall, 1), cpu_s=round(cpu, 1),
                   peak_rss_mb=round(sm.peak_mb), mean_rss_mb=round(statistics.mean(sm.samples or [0])),
                   cold_start_ms=getattr(a, "cold_ms", 0), loads=len(rec.loads))
    json.dump(dict(summary=summary, results=results, loads=rec.loads),
              open(os.path.join(OUT, f"{a.name}.json"), "w"), indent=1)
    print("SUMMARY", json.dumps(summary), flush=True)


if __name__ == "__main__":
    which = sys.argv[1]
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    targets = dict(GAP, **CONTROL)
    if len(sys.argv) > 3:  # smoke test on the first N targets
        targets = dict(list(targets.items())[: int(sys.argv[3])])
        OUT = os.path.join(S, "results", "smoke")
        os.makedirs(OUT, exist_ok=True)
    run({"plain": Plain, "chromium": Chromium, "camoufox": Camoufox}[which], passes, targets)
