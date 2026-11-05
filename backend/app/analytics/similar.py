"""Similar incidents and "which fix holds" (SPEC §7.3)."""
from __future__ import annotations

import hashlib
import re

import joblib
import numpy as np
import pandas as pd
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize as l2norm

from ..config import cache_dir
from ..db import Data, clean, inc_row
from .textnorm import normalize, signature

# deterministic fix-type rule table: (label, regex over the normalised close note, permanent?)
FIX_RULES = [
    ("Resolved by parent (upstream fix)", r"resolved by parent", False),
    ("Bagged pump / awaiting parts", r"bagged pump|until parts|prepay inside only", False),
    ("Replaced card reader board", r"card reader board", True),
    ("Replaced keypad + secure card reader", r"keypad and secure card reader", True),
    ("Remote comms reset", r"remotely reset|comms loop|remote reset", False),
    ("Rolled back change", r"rolled back|roll back", True),
    ("Hardware replaced", r"\breplaced\b|\bswapped\b|shipped replacement|replace", True),
    ("Vendor / ISP repair", r"technician|spliced|re-provisioned|vendor|isp resolved|restored primary|outage cleared|resolved a node", True),
    ("Remote restart / reset", r"restart|reboot|power-cycled|power cycle|reset", False),
    ("Re-sync / config fix", r"re-sent|re-sync|resync|re-ran|re-triggered|sync|corrected|re-activated|updated|re-imaged|refreshed|reloaded|cleared", True),
    ("Workaround in place", r"workaround|steered|manually|instructed|until next|generator connected|lane closed", False),
    ("Caller reported fixed", r"store reports|store cleared|after reboot", False),
]
_RX = [(lbl, re.compile(rx), perm) for lbl, rx, perm in FIX_RULES]
_INDEX: dict = {}


def fix_label(close_notes: str) -> str:
    t = signature(close_notes or "")
    for lbl, rx, _ in _RX:
        if rx.search(t):
            return lbl
    return "Other"


def _text(df: pd.DataFrame) -> pd.Series:
    cn = df["close_notes"].where(df["resolved_at"].notna(), "").fillna("")
    return (df["short_description"].fillna("") + " " + df["description"].fillna("") + " " + cn).map(normalize)


def build_index(data: Data):
    if "vec" in _INDEX:
        return _INDEX
    inc = data.incident
    key = hashlib.sha256(f"{len(inc)}|{inc['number'].iloc[-1]}|{inc['opened_at'].max()}|v3".encode()).hexdigest()[:16]
    path = cache_dir() / f"tfidf_{key}.joblib"
    if path.exists():
        obj = joblib.load(path)
        wv, cv, X = obj["wv"], obj["cv"], obj["X"]
        inc["fix_label"], inc["recurred_30d"] = obj["fix_label"], obj["recurred_30d"]
    else:
        texts = _text(inc)
        wv = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
        cv = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=60000)
        X = l2norm(hstack([wv.fit_transform(texts), cv.fit_transform(texts)]).tocsr())
        inc["fix_label"] = [fix_label(c) if isinstance(c, str) else None for c in inc["close_notes"]]
        _recurrence(data)
        joblib.dump(dict(wv=wv, cv=cv, X=X, fix_label=inc["fix_label"].to_numpy(), recurred_30d=inc["recurred_30d"].to_numpy()),
                    path)
    _INDEX.update(vec=(wv, cv), X=X)
    return _INDEX


def _recurrence(data: Data):
    """recurred_30d: same CI had another incident of the same issue_type within 30 days after resolution."""
    inc = data.incident
    m = inc[inc["cmdb_ci_id"].notna() & inc["store_id"].notna()].sort_values(["cmdb_ci_id", "issue_type", "opened_at"])
    nxt = m.groupby(["cmdb_ci_id", "issue_type"])["opened_at"].shift(-1)
    rec = (nxt.notna() & m["resolved_at"].notna() & (nxt > m["resolved_at"])
           & (nxt <= m["resolved_at"] + pd.Timedelta(days=30)))
    inc["recurred_30d"] = False
    inc.loc[rec.index, "recurred_30d"] = rec.to_numpy()


def _query_vec(text: str):
    wv, cv = _INDEX["vec"]
    t = [normalize(text)]
    return l2norm(hstack([wv.transform(t), cv.transform(t)]).tocsr())


def _brief(r, score=None) -> dict:
    return dict(number=r["number"], store_id=clean(r["store_id"]), cmdb_ci=clean(r["cmdb_ci"]),
                short_description=r["short_description"], issue_type=r["issue_type"], state=r["state"],
                opened_at=r["opened_at"].isoformat(), resolved_at=clean(r["resolved_at"]),
                ttr_h=clean(r["time_to_resolve_hours"]), close_code=clean(r["close_code"]),
                close_notes=clean(r["close_notes"]), fix_label=clean(r.get("fix_label")),
                recurred_30d=bool(r.get("recurred_30d", False)), problem_id=clean(r["problem_id"]),
                score=round(float(score), 3) if score is not None else None)


def find_similar(data: Data, number: str | None = None, text: str | None = None, k: int = 10,
                 store_filter=None) -> dict:
    build_index(data)
    inc = data.incident
    now = data.now()
    q = inc_row(data, number) if number else None
    if number and q is None:
        raise KeyError(number)
    if q is not None:
        qv = _INDEX["X"][data.inc_by_number[number]]
        fam = {number}
        par = q["parent_incident"] if pd.notna(q["parent_incident"]) else None
        if par:
            fam |= set(inc.loc[inc["parent_incident"] == par, "number"]) | {par}
    else:
        qv = _query_vec(text or "")
        fam = set()
    cos = (_INDEX["X"] @ qv.T).toarray().ravel()
    cand = inc["resolved_at"].notna() & ~inc["major_incident"] & ~inc["number"].isin(fam)
    if store_filter is not None:
        cand &= inc["store_id"].map(lambda s: pd.isna(s) or store_filter(int(s)))
    score = 0.55 * cos
    if q is not None:
        score = score + 0.15 * (inc["issue_type"] == q["issue_type"]).to_numpy()
        score = score + 0.10 * (inc["ci_class"] == q["ci_class"]).fillna(False).to_numpy()
        score = score + 0.10 * ((inc["ci_model"] == q["ci_model"]) & inc["ci_model"].notna()).to_numpy()
        score = score + 0.05 * (inc["store_id"] == q["store_id"]).fillna(False).to_numpy()
    days = ((now - inc["opened_at"]) / pd.Timedelta(days=1)).to_numpy()
    score = score + 0.05 * np.exp(-days / 180)
    score = np.where(cand.to_numpy(), score, -1)

    device, store, fleet = [], [], []
    if q is not None and pd.notna(q["cmdb_ci_id"]):
        dev = inc[cand & (inc["cmdb_ci_id"] == q["cmdb_ci_id"])].sort_values("opened_at", ascending=False)
        device = [_brief(r, score[i]) for i, r in dev.iterrows()]
    dev_nums = {d["number"] for d in device}
    order = np.argsort(-score)
    for i in order[:3000]:
        if score[i] < 0:
            break
        r = inc.iloc[i]
        if r["number"] in dev_nums:
            continue
        if q is not None and pd.notna(q["store_id"]) and r["store_id"] == q["store_id"]:
            if len(store) < k:
                store.append(_brief(r, score[i]))
        elif len(fleet) < k:
            fleet.append(_brief(r, score[i]))
        if len(store) >= k and len(fleet) >= k:
            break

    fix = fix_table(data, q["issue_type"] if q is not None else (fleet[0]["issue_type"] if fleet else None))
    reco = recommendation(data, q, device, fix)
    cites = [d["number"] for d in device] + [s["number"] for s in store[:3]] + [f["number"] for f in fleet[:3]]
    if reco.get("problem"):
        cites.append(reco["problem"])
    if number:
        cites.insert(0, number)
    return dict(query=number or text, issue_type=q["issue_type"] if q is not None else None,
                cmdb_ci=clean(q["cmdb_ci"]) if q is not None else None,
                tiers=dict(device=device, store=store, fleet=fleet), fix_table=fix,
                fix_table_n=int(sum(r["n"] for r in fix)), recommendation=reco["text"],
                recommendation_detail=reco, citations=list(dict.fromkeys(cites)))


def fix_table(data: Data, issue_type: str | None) -> list[dict]:
    if not issue_type:
        return []
    build_index(data)
    inc = data.incident
    g = inc[(inc["issue_type"] == issue_type) & inc["resolved_at"].notna() & inc["store_id"].notna()
            & inc["parent_incident"].isna()]
    if g.empty:
        return []
    n_all = len(g)
    rows = []
    for lbl, gg in g.groupby("fix_label"):
        if len(gg) < 3:
            continue
        rows.append(dict(fix_label=lbl, n=int(len(gg)), share=round(len(gg) / n_all, 3),
                         median_ttr_h=round(float(gg["time_to_resolve_hours"].median()), 2),
                         recurrence_30d=round(float(gg["recurred_30d"].mean()), 3),
                         permanent=any(p for l_, _, p in FIX_RULES if l_ == lbl),
                         examples=gg.sort_values("opened_at", ascending=False)["number"].head(3).tolist()))
    rows.sort(key=lambda r: (r["recurrence_30d"], r["median_ttr_h"]))
    return rows


def device_fix_table(device: list[dict]) -> list[dict]:
    rows = {}
    for d in device:
        r = rows.setdefault(d["fix_label"] or "Other", dict(fix_label=d["fix_label"] or "Other", n=0, recurred=0, numbers=[]))
        r["n"] += 1
        r["recurred"] += int(d["recurred_30d"])
        r["numbers"].append(d["number"])
    return sorted(rows.values(), key=lambda r: -r["n"])


def recommendation(data: Data, q, device: list[dict], fix: list[dict]) -> dict:
    """Deterministic recommendation sentence; cites device history and the known-error problem."""
    if q is None or not fix:
        return dict(text="Not enough history to recommend a fix.", problem=None, citations=[])
    best = fix[0]
    fast = min(fix, key=lambda r: r["median_ttr_h"])
    worst = max(fix, key=lambda r: r["recurrence_30d"])
    parts = []
    if best["recurrence_30d"] > 0 and worst["recurrence_30d"] / best["recurrence_30d"] >= 1.5:
        if fast["fix_label"] != best["fix_label"] and fast["recurrence_30d"] / best["recurrence_30d"] >= 1.5:
            parts.append(f"{fast['fix_label']} resolves faster ({fast['median_ttr_h']:.1f}h) but recurs "
                         f"{fast['recurrence_30d'] / best['recurrence_30d']:.1f}× more often than "
                         f"{best['fix_label'].lower()}.")
        else:
            parts.append(f"{worst['fix_label']} recurs {worst['recurrence_30d'] / best['recurrence_30d']:.1f}× more often "
                         f"within 30 days than {best['fix_label'].lower()} ({worst['recurrence_30d']:.0%} vs "
                         f"{best['recurrence_30d']:.0%}).")
    else:
        lo, hi = min(r["recurrence_30d"] for r in fix), max(r["recurrence_30d"] for r in fix)
        parts.append(f"Fleet-wide, the {len(fix)} fix types for this issue recur at similar rates ({lo:.0%}–{hi:.0%} "
                     f"within 30 days), so the fix type alone does not predict a lasting repair.")
    inc = data.incident
    now = data.now()
    prb, hist, dev_tab = None, [], device_fix_table(device)
    if pd.notna(q["cmdb_ci_id"]):
        h = inc[(inc["cmdb_ci_id"] == q["cmdb_ci_id"]) & (inc["opened_at"] > now - pd.Timedelta(days=365))]
        hist = h["number"].tolist()
        prbs = h["problem_id"].dropna()
        prb = prbs.mode().iat[0] if len(prbs) else (q["problem_id"] if pd.notna(q["problem_id"]) else None)
        rec_n = sum(int(d["recurred_30d"]) for d in device)
        if len(h) >= 3:
            from .chronic import RECO
            action = RECO.get(q["ci_class"], "Replace the device.")
            parts.append(f"{q['cmdb_ci']} has failed {len(h)} times in 12 months; {rec_n} of {len(device)} repairs on it "
                         f"were followed by another failure within 30 days.")
            parts.append(f"Recommendation: {action[0].lower() + action[1:]}")
        if prb:
            pr = data.problem[data.problem["number"] == prb]
            if len(pr):
                p0 = pr.iloc[0]
                txt = f"Known error {prb} ({p0['state']}): {p0['short_description']}."
                if isinstance(p0["workaround"], str):
                    txt += f" Workaround: {p0['workaround']}"
                if isinstance(p0["fix_notes"], str):
                    txt += f" Permanent fix: {p0['fix_notes']}"
                parts.append(txt)
    elif pd.notna(q["problem_id"]):
        prb = q["problem_id"]
    return dict(text=" ".join(parts), best_fix=best["fix_label"], fastest_fix=fast["fix_label"], problem=prb,
                device_history=hist, device_failures_365d=len(hist), device_fix_table=dev_tab,
                citations=hist + ([prb] if prb else []))
