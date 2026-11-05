"""The scripted §11.2 demo flow, shared by e2e tests, screenshots and the backup video.

Each step: (key, caption, action(page, ctx)). Assertions run when check=True.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def meta(base):
    return json.loads(urllib.request.urlopen(f"{base}/api/meta").read())


def api(base, path, persona):
    req = urllib.request.Request(f"{base}{path}", headers={"X-Persona-Id": persona})
    return json.loads(urllib.request.urlopen(req).read())


CAPTION_JS = """(t) => { let d = document.getElementById('sl-caption');
  if (!d) { d = document.createElement('div'); d.id = 'sl-caption';
    d.style.cssText = 'position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:9999;max-width:70%;'
      + 'background:rgba(8,12,20,.88);color:#fff;font:500 18px "IBM Plex Sans",sans-serif;padding:10px 18px;'
      + 'border-radius:10px;border:1px solid rgba(255,255,255,.15);text-align:center;pointer-events:none';
    document.body.appendChild(d); }
  d.textContent = t; d.style.display = t ? 'block' : 'none'; }"""


class Flow:
    def __init__(self, page, base: str, pace: float = 1.0, captions: bool = False, check: bool = True, shot=None):
        self.p, self.base, self.pace, self.captions, self.check, self.shot = page, base, pace, captions, check, shot
        self.m = meta(base)
        self.demo_store = self.m["demo"]["store_id"]
        self.engineer = self.m["default_personas"]["engineer"]
        self.exec = self.m["default_personas"]["exec"]
        opens = self.m["demo"]["open_incidents"]
        h = api(base, f"/api/stores/{self.demo_store}/health", self.engineer)
        self.fuel = next(o["number"] for o in h["open_incidents"] if o["category"] == "Fuel")
        self.opens = opens

    # ------------------------------------------------------------ helpers
    def wait(self, s):
        self.p.wait_for_timeout(int(s * 1000 * self.pace))

    def cap(self, text):
        if self.captions:
            self.p.evaluate(CAPTION_JS, text)

    def answer_done(self, timeout=20000):
        self.p.wait_for_function("""() => { const a = document.querySelectorAll('[data-testid=answer]');
            const t = document.querySelectorAll('[data-turn]'); return t.length && a.length >= t.length; }""", timeout=timeout)

    def ask(self, q):
        self.p.fill('[data-testid=ask-input]', "")
        self.p.type('[data-testid=ask-input]', q, delay=int(35 * self.pace) if self.pace > 0.3 else 0)
        self.p.keyboard.press("Enter")
        self.answer_done()

    def click_text(self, sel, text):
        self.p.locator(sel, has_text=text).first.click()

    def snap(self, key):
        if self.shot:
            self.shot(key)

    def assert_text(self, *needles):
        if not self.check:
            return
        body = self.p.inner_text("body").lower()
        for n in needles:
            assert n.lower() in body, f"expected '{n}' on page"

    # ------------------------------------------------------------ the flow
    def run(self):
        p = self.p
        urllib.request.urlopen(urllib.request.Request(f"{self.base}/api/admin/reset", method="POST"))
        p.goto(self.base + "/")
        p.evaluate(f"() => {{ localStorage.setItem('sl.persona', '{self.engineer}'); localStorage.setItem('sl.theme', 'dark'); }}")
        p.reload()
        p.wait_for_selector('[data-testid=major-banner]')
        self.cap("Noon on a Wednesday. Here's what our NOC sees.")
        self.wait(2.5)
        self.assert_text("MAJOR", "54 stores")
        self.snap("01_banner")

        self.cap("Type a store number…")
        p.click('[data-testid=command-bar]')
        p.type('[data-testid=command-bar]', str(self.demo_store), delay=int(90 * self.pace) if self.pace > 0.3 else 0)
        p.keyboard.press("Enter")
        p.wait_for_selector('[data-testid=health-card]')
        p.wait_for_selector('[data-testid=chronic-alert]')
        self.cap(f"Store {self.demo_store}, Dallas: one screen — profile, services, everything open.")
        self.wait(3)
        self.assert_text(str(self.demo_store), "Critical", "Pump 3 card reader")
        self.snap("02_health")

        self.cap("“What's going on at this store?”")
        self.ask("What's going on at this store?")
        self.wait(1.5)
        self.cap("No ticket-hopping. Every line is cited — hover any chip.")
        chip = p.locator('[data-testid=answer] button[aria-label^="Open INC"]').first
        chip.hover()
        self.wait(2.5)
        self.assert_text("part of major", "Spectrum")
        self.snap("03_answer")
        p.mouse.move(5, 500)

        self.cap("“Is this affecting other stores?”")
        self.click_text('[data-testid=followup]', "Is this affecting other stores?")
        self.answer_done()
        p.wait_for_selector('[data-testid=blast-map]')
        self.cap("54 stores hit — and the common thread: one internet provider.")
        self.wait(3.5)
        self.assert_text("Likely common cause", "Spectrum Business")
        self.snap("04_blast")

        self.cap("Parent and child tickets stitched into one story.")
        p.click('[data-testid=tab-timeline]')
        p.wait_for_selector('[data-testid=timeline]')
        self.wait(3)
        self.assert_text("stores opened tickets")
        self.snap("05_timeline")

        self.cap("Pump 3: “What fixed this last time?”")
        p.click(f'[data-testid=open-{self.fuel}]')
        self.wait(0.8)
        self.ask("What fixed this last time?")
        p.wait_for_selector('[data-testid=fixes]')
        self.cap("Not just the last fix — the fix that sticks (and when nothing sticks, replace the part).")
        self.wait(4)
        self.assert_text("Failure #9", "Which fix holds")
        self.snap("06_fixes")

        self.cap("“Did anything change here recently?”")
        self.ask("Did anything change here recently?")
        p.wait_for_selector('[data-testid=changes]')
        self.cap("It rules things out too — no change caused this. A PIN-pad visit is already scheduled.")
        self.wait(3.5)
        self.assert_text("No changes touched", "PIN pad refresh")
        self.snap("07_changes")

        self.cap("Same store, the VP's view: dollars first, no IPs — enforced in the data layer.")
        p.click('[data-testid=persona-switcher]')
        self.wait(0.6)
        p.click(f'[data-testid=persona-exec-{self.exec}]')
        p.wait_for_selector('[data-testid=health-card]')
        self.wait(1)
        self.click_text('[data-testid=suggestion]', "What's going on at this store?")
        self.answer_done()
        self.wait(3)
        if self.check:
            body = p.inner_text("body")
            m = IPV4.search(body)
            assert not m, f"exec view must not show IPs: {body[max(0, m.start() - 80):m.end() + 20]!r}"
            assert "$" in body
        self.snap("08_exec")

        self.cap("“How much is the DFW outage costing us?”")
        p.click('[data-testid=major-banner] button >> nth=0')
        self.wait(1)
        self.ask("How much is the DFW outage costing us?")
        self.cap("Ten thousand dollars and climbing — every hour.")
        self.wait(3.5)
        self.assert_text("est. lost sales")
        self.snap("09_cost")

        self.cap("At shift change, the handoff writes itself — straight into Teams.")
        p.click('nav >> text=Operations')
        p.wait_for_selector('[data-testid=handoff]')
        p.select_option('[data-testid=handoff-region]', "Texas")
        self.wait(1.5)
        p.click('[data-testid=preview-teams]')
        self.wait(3.5)
        self.assert_text("Shift handoff")
        self.snap("10_handoff_teams")
        self.cap("")
        p.keyboard.press("Escape")
        return True
