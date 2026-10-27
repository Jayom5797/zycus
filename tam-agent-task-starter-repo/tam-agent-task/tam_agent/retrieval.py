"""KB retrieval. Built once per run; the thing that must scale.

Why this exists
---------------
The KB is ~386 articles / ~100k tokens. We must never paste it into a prompt.
Instead we build three cheap indexes once and answer each query with a small,
ranked candidate list of *headers + short excerpts* - not full bodies.

Indexes (all built once, reused for every ticket):
  1. BM25 over `title + body` (pure Python, zero dependencies, fully explainable).
  2. error_code -> [article ids]   (exact code match is the strongest signal).
  3. canonical module -> [article ids]  (after alias normalization).

Ranking honours article lifecycle, which is critical for the review set:
  active_incident > current > resolved >> deprecated
  - E-4102 must surface the active incident, not the generic troubleshooter.
  - E-2207 must surface the CURRENT article, never the deprecated one.

Design choices (for the walkthrough):
  * BM25 from scratch, not a library: fewer deps, every line defensible, and
    386 docs is tiny so performance is a non-issue.
  * Return excerpts, not bodies, to keep tokens down (guardrail S3). The agent
    pulls a full body via a separate tool only when it needs to decide.
  * At 10x (4,000 articles) we'd swap BM25 for an embedding/ANN index behind the
    same search() signature - the agent code would not change. That is the Scale
    story, not something we over-build now.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

# Lifecycle weighting. Higher = preferred. Deprecated is strongly demoted so it
# never wins over a current/resolved successor, but is not fully removed (the
# agent may still follow a superseded_by link deliberately).
STATUS_WEIGHT = {
    "active_incident": 1.6,
    "current": 1.0,
    "resolved": 0.7,
    "deprecated": 0.15,
}

# Ticket `module` is free text with many aliases. Map every observed variant to
# one of the 8 canonical KB modules (plus "All"). Derived from the actual data.
_MODULE_ALIASES = {
    "platform": "Platform", "sso": "Platform", "login": "Platform",
    "contracts": "Contracts", "contract": "Contracts", "clm": "Contracts",
    "contract mgmt": "Contracts",
    "sourcing": "Sourcing", "rfx": "Sourcing", "esourcing": "Sourcing",
    "sourcing events": "Sourcing", "auction": "Sourcing",
    "spend analytics": "Spend Analytics", "spend": "Spend Analytics",
    "analytics": "Spend Analytics", "reports": "Spend Analytics",
    "supplier management": "Supplier Management", "supplier mgmt": "Supplier Management",
    "sim": "Supplier Management", "supplier portal": "Supplier Management",
    "eprocurement": "eProcurement", "eproc": "eProcurement",
    "e-procurement": "eProcurement", "procurement": "eProcurement",
    "einvoice": "eInvoice", "e-invoice": "eInvoice", "invoicing": "eInvoice",
    "invoice": "eInvoice", "ap": "eInvoice", "einv": "eInvoice", "ocr": "eInvoice",
    "integrations": "Integrations", "integration": "Integrations",
    "erp integration": "Integrations", "api": "Integrations", "connector": "Integrations",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CODE_RE = re.compile(r"\bE-\d{3,5}\b", re.I)
_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are",
    "this", "that", "it", "we", "our", "with", "as", "by", "be", "at", "from",
    "when", "if", "not", "no", "can", "do", "does", "how", "what", "please",
}


def normalize_module(raw: str | None) -> str | None:
    """Map a free-text ticket module to a canonical KB module, or None if unknown."""
    if not raw:
        return None
    key = raw.strip().lower()
    if key in _MODULE_ALIASES:
        return _MODULE_ALIASES[key]
    # exact canonical match (case-insensitive)
    for canon in ("Platform", "Contracts", "Sourcing", "Spend Analytics",
                  "Supplier Management", "eProcurement", "eInvoice", "Integrations"):
        if key == canon.lower():
            return canon
    return None


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP]


def extract_error_codes(text: str | None) -> list[str]:
    """Pull error codes like E-4102 from free text, upper-cased, de-duplicated."""
    seen, out = set(), []
    for m in _CODE_RE.findall(text or ""):
        c = m.upper()
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


class KBIndex:
    """BM25 + error-code + module indexes over the KB. Built once per run."""

    def __init__(self, kb: list[dict], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.articles = {a["id"]: a for a in kb}

        self._docs: dict[str, list[str]] = {}       # id -> tokens
        self._df: Counter = Counter()                # token -> doc frequency
        self._len: dict[str, int] = {}               # id -> token count
        self.by_code: dict[str, list[str]] = defaultdict(list)
        self.by_module: dict[str, list[str]] = defaultdict(list)

        for a in kb:
            aid = a["id"]
            meta = a["meta"]
            tokens = _tokenize(f"{meta.get('title', '')} {a.get('body', '')}")
            self._docs[aid] = tokens
            self._len[aid] = len(tokens)
            for tok in set(tokens):
                self._df[tok] += 1
            for code in (meta.get("error_codes") or []):
                self.by_code[str(code).upper()].append(aid)
            mod = meta.get("module")
            if mod:
                self.by_module[mod].append(aid)

        self.N = len(kb)
        self.avgdl = (sum(self._len.values()) / self.N) if self.N else 0.0

    # -- BM25 ---------------------------------------------------------------
    def _bm25(self, query_tokens: list[str], aid: str) -> float:
        if not query_tokens:
            return 0.0
        doc = self._docs[aid]
        if not doc:
            return 0.0
        tf = Counter(doc)
        dl = self._len[aid]
        score = 0.0
        for q in query_tokens:
            n_q = self._df.get(q, 0)
            if n_q == 0:
                continue
            idf = math.log(1 + (self.N - n_q + 0.5) / (n_q + 0.5))
            f = tf.get(q, 0)
            if f == 0:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            score += idf * (f * (self.k1 + 1)) / denom
        return score

    # -- excerpt ------------------------------------------------------------
    @staticmethod
    def _excerpt(body: str, query_tokens: list[str], width: int = 220) -> str:
        """Short snippet around the first query-term hit; keeps tokens low."""
        text = re.sub(r"\s+", " ", (body or "").strip())
        if not text:
            return ""
        low = text.lower()
        pos = -1
        for q in query_tokens:
            i = low.find(q)
            if i != -1:
                pos = i
                break
        if pos == -1:
            return text[:width] + ("…" if len(text) > width else "")
        start = max(0, pos - width // 3)
        snippet = text[start:start + width]
        return ("…" if start > 0 else "") + snippet + ("…" if start + width < len(text) else "")

    # -- public search ------------------------------------------------------
    def search(self, query: str, module: str | None = None,
               error_code: str | None = None, k: int = 5) -> list[dict]:
        """Return up to k candidate headers + short excerpts, best first.

        Scoring = BM25(text) * status_weight, with additive boosts for an exact
        error-code match and a canonical module match. Returns metadata only,
        never full bodies (the agent fetches a body via get_kb_article).
        """
        q_tokens = _tokenize(query)
        canon_mod = normalize_module(module)
        code = (error_code or "").upper().strip() or None

        # Candidate set: strong signals (code/module) union the whole KB for text.
        code_hits = set(self.by_code.get(code, [])) if code else set()
        mod_hits = set(self.by_module.get(canon_mod, [])) if canon_mod else set()

        scored = []
        for aid, art in self.articles.items():
            meta = art["meta"]
            status = meta.get("status", "current")
            base = self._bm25(q_tokens, aid)
            boost = 0.0
            if aid in code_hits:
                # An exact error-code match is the most precise signal we have:
                # the code identifies the canonical article for that fault. Make
                # it dominate text similarity, so e.g. E-3310 ranks KB-4795 (the
                # E-3310 article) above a textually-similar fix article the agent
                # can still reach by following `related`.
                boost += 18.0
            if aid in mod_hits:
                boost += 1.5          # right module breaks ties
            if base == 0.0 and boost == 0.0:
                continue
            score = (base + boost) * STATUS_WEIGHT.get(status, 1.0)
            scored.append((score, aid))

        scored.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for score, aid in scored[:k]:
            art = self.articles[aid]
            meta = art["meta"]
            out.append({
                "id": aid,
                "title": meta.get("title", ""),
                "type": meta.get("type", ""),
                "status": meta.get("status", ""),
                "module": meta.get("module", ""),
                "error_codes": meta.get("error_codes") or [],
                "superseded_by": meta.get("superseded_by") or None,
                "related": meta.get("related") or [],
                "excerpt": self._excerpt(art.get("body", ""), q_tokens),
                "score": round(float(score), 3),
            })
        return out

    def get(self, kb_id: str) -> dict | None:
        """Full article (meta + body) for a specific id, or None."""
        return self.articles.get(kb_id)
