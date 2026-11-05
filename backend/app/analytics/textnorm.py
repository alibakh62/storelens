"""Text normalisation for similarity / fix signatures (SPEC §7.3)."""
import re

_RULES = [
    (re.compile(r"\bS\d{5}-[A-Z0-9]+-\d{2}\b"), " <ci> "),
    (re.compile(r"\b(?:INC|CHG|PRB|KB)\d{7}\b"), " <ref> "),
    (re.compile(r"\b[A-Z]{2,5}-\d{6,8}\b"), " <ref> "),
    (re.compile(r"\b[A-Z]{2,5}-\d{4}-\d{6}\b"), " <ref> "),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"), " <ip> "),
    (re.compile(r"\b(?:v|MX\s?|MS\s?)?\d+(?:\.\d+){1,3}\b"), " <ver> "),
    (re.compile(r"\b\d{1,2}:\d{2}\b"), " <time> "),
    (re.compile(r"\b\d{5}\b"), " <store> "),
]


def normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    for rx, tok in _RULES:
        text = rx.sub(tok, text)
    text = text.lower()
    return re.sub(r"\s+", " ", text).strip()


def signature(text: str) -> str:
    t = normalize(text)
    t = re.sub(r"\d+", "", t)
    return re.sub(r"[^a-z<> ]", " ", re.sub(r"\s+", " ", t)).strip()
