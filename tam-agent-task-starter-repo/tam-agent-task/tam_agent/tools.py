"""The agent's tools: the only way it touches data.

Each tool is a small, deterministic function that returns a COMPACT,
JSON-serializable result - just the fields needed for the next decision
(guardrails S2C / S3). The agent never receives the whole ticket table, the
whole KB, or full account rows.

Determinism matters: dates, renewal windows, usage trends and the duplicate
window are computed in Python, not left to the model (guardrail S4). The LLM
decides *which* tool to call and *what to conclude*; it does not do arithmetic
on dates or invent account facts.

Tools exposed to the agent:
  resolve_account      account_id (may be blank) or requester_domain -> account
  get_account_health   renewal window + usage trend + escalations (risk signals)
  find_related_tickets duplicate-window candidates (same acct/module/code, 7d)
  search_kb            top-k KB headers + excerpts (via retrieval.KBIndex)
  get_kb_article       one full article body (for a specific decision / to follow links)
  get_policy           the 3 policy articles, trimmed (priority, duplicates, data exposure)
"""
from __future__ import annotations

from datetime import date, datetime

from .retrieval import KBIndex, normalize_module, extract_error_codes

AS_OF = date(2026, 10, 20)  # fixed "today" per data/README.md

# The three policy articles. IDs are stable; we load bodies from the KB index.
POLICY_IDS = {
    "priority_escalation": "KB-2914",
    "duplicates": "KB-5930",
    "data_exposure": "KB-6401",
}


def _to_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _int(s, default=0) -> int:
    try:
        return int(str(s).strip())
    except (ValueError, AttributeError):
        return default


class Tools:
    """Holds the loaded data + KB index; exposes the agent's callable tools.

    Built once per run and shared across tickets. `seen_kb` tracks which full
    articles have already been handed to the model this ticket, so we don't
    resend bodies (token guardrail S3). It is reset per ticket by the agent.
    """

    def __init__(self, accounts, tickets, usage, kb):
        self.accounts = accounts
        self.tickets = tickets
        self.usage = usage
        self.index = KBIndex(kb)
        # fast lookups
        self._acc_by_id = {r["account_id"]: r for r in accounts.to_dict("records")}
        self._acc_by_domain = {r["domain"]: r for r in accounts.to_dict("records")}
        self.seen_kb: set[str] = set()

    # -- account resolution -------------------------------------------------
    def resolve_account(self, account_id: str | None = None,
                        requester_domain: str | None = None) -> dict:
        """Resolve an account. Prefer the explicit account_id; fall back to the
        requester_domain (authoritative 1:1 map) when account_id is blank.

        Returns compact identity + the fields policy cares about. `resolved_by`
        makes the resolution path auditable in the trace.
        """
        rec = None
        resolved_by = None
        if account_id and str(account_id).strip():
            rec = self._acc_by_id.get(str(account_id).strip())
            resolved_by = "account_id"
        if rec is None and requester_domain:
            rec = self._acc_by_domain.get(str(requester_domain).strip())
            resolved_by = "requester_domain" if rec else None
        if rec is None:
            return {"resolved": False, "account_id": None,
                    "note": "No account for the given id/domain."}
        return {
            "resolved": True,
            "resolved_by": resolved_by,
            "account_id": rec["account_id"],
            "company_name": rec["company_name"],
            "tier": rec["tier"],
            "renewal_date": rec["renewal_date"],
            "open_escalations": _int(rec["open_escalations"]),
            "modules_licensed": rec["modules_licensed"],
        }

    # -- account health / risk signals -------------------------------------
    def get_account_health(self, account_id: str) -> dict:
        """Deterministic risk signals used by the account-risk override (KB-2914):
        days to renewal, 3-month active-user trend, and open escalations.

        The override rule (encoded later in policy.py, not here) is:
          within 90 days of renewal AND (users down >25% over 3 months
          OR >= 2 open escalations).
        This tool only reports the facts; policy decides.
        """
        rec = self._acc_by_id.get(str(account_id).strip())
        if rec is None:
            return {"found": False, "account_id": account_id}

        renewal = _to_date(rec["renewal_date"])
        days_to_renewal = (renewal - AS_OF).days if renewal else None
        within_90d = (days_to_renewal is not None and 0 <= days_to_renewal <= 90)

        rows = self.usage[self.usage["account_id"] == str(account_id).strip()]
        rows = rows.sort_values("month")
        months = rows["month"].tolist()
        users = rows["active_users"].tolist()

        change_pct = None
        decline_gt_25 = False
        if len(users) >= 4:
            recent = users[-1]
            prior = users[-4]  # 3 months earlier
            if prior:
                change_pct = round((recent - prior) / prior * 100, 1)
                decline_gt_25 = change_pct <= -25.0

        return {
            "found": True,
            "account_id": rec["account_id"],
            "tier": rec["tier"],
            "renewal_date": rec["renewal_date"],
            "days_to_renewal": days_to_renewal,
            "within_90d_renewal": within_90d,
            "active_user_change_pct_3m": change_pct,
            "usage_decline_gt_25pct_3m": decline_gt_25,
            "open_escalations": _int(rec["open_escalations"]),
            "recent_months": months[-3:],
            "recent_active_users": users[-3:],
        }

    # -- duplicate detection ------------------------------------------------
    def find_related_tickets(self, account_id: str, module: str | None = None,
                             error_code: str | None = None, created_at: str | None = None,
                             within_days: int = 7, exclude_ticket_id: str | None = None) -> dict:
        """Find candidate ORIGINAL tickets for the duplicate rule (KB-5930):
        same account, same module or error/symptom, open/pending, created in the
        previous `within_days` days (default 7) *before* this ticket.

        `exclude_ticket_id` drops the ticket being triaged so it can never be
        reported as its own duplicate. Returns a short candidate list; the model
        (and policy) decide if it is a true duplicate. Never returns other
        accounts' tickets.
        """
        acc = str(account_id).strip()
        this_created = _to_date(created_at) or AS_OF
        canon_mod = normalize_module(module)
        code = (error_code or "").upper().strip() or None
        this_id = (exclude_ticket_id or "").strip()

        df = self.tickets[self.tickets["account_id"] == acc]
        candidates = []
        for r in df.to_dict("records"):
            if r.get("ticket_id") == this_id:
                continue  # never match the ticket against itself
            if r.get("status") not in ("open", "pending_customer"):
                continue
            created = _to_date(r.get("created_at"))
            if not created:
                continue
            age = (this_created - created).days
            if age < 0 or age > within_days:
                continue
            r_mod = normalize_module(r.get("module"))
            r_codes = extract_error_codes(
                f"{r.get('error_code','')} {r.get('description','')} {r.get('subject','')}")
            same_code = bool(code and code in r_codes)
            same_mod = bool(canon_mod and r_mod == canon_mod)
            if not (same_code or same_mod):
                continue
            candidates.append({
                "ticket_id": r["ticket_id"],
                "created_at": r.get("created_at"),
                "status": r.get("status"),
                "module": r.get("module"),
                "match": "error_code" if same_code else "module",
                "subject": (r.get("subject") or "")[:80],
            })
        # most recent first, cap the list
        candidates.sort(key=lambda c: c["created_at"], reverse=True)
        return {"account_id": acc, "window_days": within_days,
                "candidates": candidates[:5]}

    # -- KB search ----------------------------------------------------------
    def search_kb(self, query: str, module: str | None = None,
                  error_code: str | None = None, k: int = 5) -> dict:
        """Top-k KB headers + short excerpts. Status-aware ranking; exact
        error-code matches dominate. Returns metadata only, not full bodies."""
        results = self.index.search(query, module=module, error_code=error_code, k=k)
        return {"query": query, "module": normalize_module(module),
                "error_code": (error_code or None), "results": results}

    def get_kb_article(self, kb_id: str) -> dict:
        """Full body for one article, to resolve a specific decision or to follow
        a `related` / `superseded_by` link (multi-hop). De-duplicated per ticket:
        if already fetched, we say so instead of resending the body."""
        art = self.index.get(str(kb_id).strip())
        if art is None:
            return {"found": False, "id": kb_id}
        meta = art["meta"]
        already = kb_id in self.seen_kb
        self.seen_kb.add(kb_id)
        out = {
            "found": True,
            "id": meta.get("id"),
            "title": meta.get("title"),
            "type": meta.get("type"),
            "status": meta.get("status"),
            "module": meta.get("module"),
            "error_codes": meta.get("error_codes") or [],
            "superseded_by": meta.get("superseded_by") or None,
            "related": meta.get("related") or [],
        }
        if already:
            out["note"] = "Already retrieved earlier this ticket; body omitted."
        else:
            out["body"] = art.get("body", "")
        return out

    def get_policy(self) -> dict:
        """The three policy articles, bodies included. The brief says to start
        here; they define priority, routing, escalation, duplicates and data
        exposure. Returned once; the agent keeps them in context thereafter."""
        out = {}
        for name, kid in POLICY_IDS.items():
            art = self.index.get(kid)
            if art:
                out[name] = {"id": kid, "title": art["meta"].get("title"),
                             "body": art.get("body", "")}
                self.seen_kb.add(kid)
        return out
