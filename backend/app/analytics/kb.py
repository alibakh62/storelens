"""KB search (SPEC §7.11): TF-IDF over short_description + symptoms + resolution_steps."""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer

from ..db import Data
from .textnorm import normalize

_KB: dict = {}


def _index(data: Data):
    if not _KB:
        kb = data.kb
        texts = (kb["short_description"].fillna("") + " " + kb["symptoms"].fillna("") + " " +
                 kb["resolution_steps"].fillna("")).map(normalize)
        vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
        _KB.update(vec=vec, X=vec.fit_transform(texts))
    return _KB


def search_kb(data: Data, query: str, issue_type: str | None = None, k: int = 3) -> dict:
    idx = _index(data)
    kb = data.kb
    sims = (idx["X"] @ idx["vec"].transform([normalize(query or "")]).T).toarray().ravel()
    if issue_type:
        sims = sims + 0.5 * (kb["issue_type"] == issue_type).to_numpy()
    order = sims.argsort()[::-1][:k]
    hits = []
    for i in order:
        r = kb.iloc[i]
        if sims[i] <= 0:
            continue
        steps = str(r["resolution_steps"])
        hits.append(dict(number=r["number"], short_description=r["short_description"], issue_type=r["issue_type"],
                         kb_type=r["kb_type"], score=round(float(sims[i]), 3), symptoms=r["symptoms"],
                         resolution_steps=steps, snippet=steps[:220] + ("…" if len(steps) > 220 else ""),
                         related_problem=r["related_problem"] if isinstance(r["related_problem"], str) else None,
                         escalation_group=r["escalation_group"]))
    return dict(query=query, hits=hits, citations=[h["number"] for h in hits])
