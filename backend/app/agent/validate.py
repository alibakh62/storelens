"""The anti-hallucination layer (SPEC §8.6): citation check, numeric grounding, RBAC re-check."""
from __future__ import annotations

import bisect
import copy
import json
import math
import re

from ..rbac import CIRCUIT, IPV4, RESTRICTED, Scope

ID_RX = re.compile(r"\b(?:INC|CHG|PRB|KB)\d{7}\b")
STRIP = [
    ID_RX,
    re.compile(r"\bS\d{5}-[A-Z0-9]+-\d{2}\b"),                       # CI names
    re.compile(r"\b[A-Z]{2,5}-\d{4}-\d{6}\b"),                       # circuit ids
    re.compile(r"\b[A-Z]{2,5}-\d{6,8}\b"),                           # vendor refs
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+)?\b"),            # ISO dates
    re.compile(r"\b\d{1,2}:\d{2}(?:\s?[AaPp]\.?[Mm]\.?)?(?:\s?(?:CT|ET|MT|PT|UTC))?"),  # clock times
    re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b"),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"),         # IPs (checked by RBAC instead)
    re.compile(r"\b(?:v|MX\s?|MS\s?)?\d+\.\d+\.\d+\b"),             # versions
    re.compile(r"\bP[1-5]\b"),                                      # priority labels
    re.compile(r"\b[1-5] - (?:Critical|High|Moderate|Low|Planning)\b"),
    re.compile(r"\b(?:24/7|7NOW|7MD|7-Eleven|3-?\s?liner|30-day|90-day|12-month|365-day|72h|24h|S\d\b)"),
    re.compile(r"\b(?:last|next|within|past|the)\s+(?:24|72|12|30|90|7|5|6)\s?(?:h|hours?|days?|d|months?|weeks?)\b", re.I),
    re.compile(r"\b(?:95)%\s?CI\b"),
    re.compile(r"\bp\s?[<≤]\s?0\.0[15]\b"),
    re.compile(r"\b[A-Z]-[A-Z]{2}-[A-Z]{3}-\d{2}\b"),                # district ids
]
NUM_RX = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?([kKmM](?![a-z])|%|×|x\b)?")


def _walk(obj, nums: list, ids: set, texts: list):
    if isinstance(obj, dict):
        for v in obj.values():
            _walk(v, nums, ids, texts)
    elif isinstance(obj, list):
        nums.append(float(len(obj)))
        for v in obj:
            _walk(v, nums, ids, texts)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        if not (isinstance(obj, float) and math.isnan(obj)):
            nums.append(float(obj))
    elif isinstance(obj, str):
        ids.update(ID_RX.findall(obj))
        for m in re.finditer(r"\d+(?:\.\d+)?", obj.replace(",", "")):
            try:
                nums.append(float(m.group(0)))
            except ValueError:
                pass


class Ground:
    def __init__(self, tool_results: list):
        nums, ids, texts = [], set(), []
        for r in tool_results:
            _walk(r, nums, ids, texts)
        # percentages stored as shares, ratios, and count-of-stores style derived values
        ext = []
        for n in nums:
            ext.append(n)
            if abs(n) <= 1.0:
                ext.append(n * 100)
        self.nums = sorted(set(round(x, 6) for x in ext))
        self.ids = ids

    def has(self, x: float, decimals: int, mult: float) -> bool:
        tol_abs = 0.5 * (10 ** -decimals) * mult + 1e-9
        lo, hi = x - max(tol_abs, 0.01 * abs(x)), x + max(tol_abs, 0.01 * abs(x))
        i = bisect.bisect_left(self.nums, lo)
        return i < len(self.nums) and self.nums[i] <= hi


def numbers_in(text: str) -> list[tuple[str, float, int, float]]:
    t = text
    for rx in STRIP:
        t = rx.sub(" ", t)
    out = []
    for m in NUM_RX.finditer(t):
        raw, suf = m.group(1), (m.group(2) or "")
        val = float(raw.replace(",", ""))
        dec = len(raw.split(".")[1]) if "." in raw else 0
        mult = 1.0
        if suf.lower() == "k":
            mult = 1e3
        elif suf.lower() == "m":
            mult = 1e6
        out.append((m.group(0), val * mult, dec, mult))
    return out


def _bullet_ok(text: str, cites: list, g: Ground, need_cite: bool, report: dict, where: str,
               sources: list | None = None, tools_called: set | None = None) -> bool:
    found = set(ID_RX.findall(text)) | set(cites or [])
    unknown = [c for c in found if c not in g.ids]
    if unknown:
        report["removed_bullets"].append(dict(where=where, text=text, reason=f"unknown id {unknown[0]}"))
        return False
    sourced = bool(sources) and tools_called is not None and set(sources) <= tools_called
    if need_cite and not found and not sourced:
        report["removed_bullets"].append(dict(where=where, text=text, reason="no citation"))
        return False
    for raw, val, dec, mult in numbers_in(text):
        if val in (0.0, 1.0) and mult == 1.0:
            continue
        if not g.has(val, dec, mult):
            report["ungrounded_numbers"].append(dict(where=where, token=raw.strip(), text=text))
            report["removed_bullets"].append(dict(where=where, text=text, reason=f"ungrounded number {raw.strip()}"))
            return False
    report["citations_checked"] += len(found)
    return True


def _rbac_text(text: str, scope: Scope, report: dict) -> str:
    if scope.can_view_network:
        return text
    def sub(m):
        report["rbac_redactions"] += 1
        return RESTRICTED
    return CIRCUIT.sub(sub, IPV4.sub(sub, text))


def validate(answer: dict, tool_results: list, scope: Scope, extra_results: list | None = None,
             tools_called: set | None = None) -> tuple[dict, dict]:
    """tools_called: names of tools run this turn — a bullet may cite a non-record source (e.g. the store profile)."""
    g = Ground(list(tool_results) + list(extra_results or []))
    a = copy.deepcopy(answer)
    rep = dict(citations_checked=0, removed_bullets=[], ungrounded_numbers=[], rbac_redactions=0)

    # RBAC re-check over all text first
    def scrub(o):
        if isinstance(o, dict):
            return {k: scrub(v) for k, v in o.items()}
        if isinstance(o, list):
            return [scrub(v) for v in o]
        if isinstance(o, str):
            return _rbac_text(o, scope, rep)
        return o
    a = scrub(a)

    a["bullets"] = [b for b in a.get("bullets", []) if _bullet_ok(b.get("text", ""), b.get("citations"), g, True, rep, "bullets",
                                                                b.get("sources"), tools_called)]
    for s in a.get("sections", []) or []:
        s["bullets"] = [b for b in s.get("bullets", []) if _bullet_ok(b.get("text", ""), b.get("citations"), g, True, rep,
                                                                      s.get("title", "section"), b.get("sources"), tools_called)]
    a["sections"] = [s for s in a.get("sections", []) or [] if s.get("bullets")]
    a["recommended_actions"] = [b for b in a.get("recommended_actions", []) or []
                                if _bullet_ok(b.get("text", ""), b.get("citations"), g, True, rep, "actions",
                                              b.get("sources"), tools_called)]
    # followups: no citation needed, but they must not carry invented ids
    a["followups"] = [f for f in a.get("followups", []) or [] if all(i in g.ids for i in ID_RX.findall(f))]
    hl = a.get("headline", "")
    hl_rep = dict(citations_checked=0, removed_bullets=[], ungrounded_numbers=[], rbac_redactions=0)
    if not _bullet_ok(hl, [], g, False, hl_rep, "headline"):
        rep["ungrounded_numbers"] += hl_rep["ungrounded_numbers"]
        a["headline"] = re.sub(r"\$?\d[\d,.]*\s?[kKmM%×]?", "", ID_RX.sub(lambda m: m.group(0) if m.group(0) in g.ids else "",
                                                                            hl)).strip(" -—:,") or "Here's what I found"
    rep["removed_count"] = len(rep["removed_bullets"])
    rep["ungrounded_count"] = len(rep["ungrounded_numbers"])
    if not a["bullets"] and not a.get("sections"):
        a["confidence"] = "low"
    return a, rep


def tool_digest(results: list) -> str:
    return json.dumps(results, sort_keys=True, default=str)[:200]
