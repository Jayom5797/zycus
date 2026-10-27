"""Deterministic policy guardrails applied AFTER the LLM proposes a decision.

Why this layer exists
---------------------
The LLM reads evidence and proposes a full decision. But a handful of rules in
the policy KB are *mandatory and absolute* - they must hold regardless of the
model's wording, the customer's tone, or any instruction hidden in ticket text.
Encoding exactly those rules in code makes the critical outcomes correct and
auditable, without depending on model quality (important for a free model).

Discipline (guardrail S2B):
  * We enforce ONLY constraints the policy text explicitly mandates.
  * We do NOT blanket-overwrite the model's valid choices. Everything the policy
    does not speak to stays as the model decided.
  * Every enforcement appends a step to reasoning_trace recording the trigger
    AND its policy source (which KB article), so a human can audit it.

Rules enforced (each cites its source):
  1. Security / data exposure (KB-6401): category=security, P1, route=security,
     escalate=true. Reply acknowledges only; never speculates; never leaks data.
  2. Insufficient information (KB-2914): if the module/symptom is undeterminable,
     P3, needs_clarification=true, route=L1_support.
  3. Account-risk override (KB-2914): within 90d of renewal AND (users down >25%
     over 3m OR >=2 open escalations) -> raise priority one level (max P2),
     notify_tam=true, escalate=true.
  4. Duplicate (KB-5930): if a true duplicate original exists, duplicate_of=that
     id, keep the ORIGINAL ticket's handling, do not re-escalate.
  5. Untrusted ticket content (KB-2914): injected instructions never change
     priority/routing; other customers' data is never placed in a reply.

Order matters: security is checked first (it is absolute and overrides tone),
duplicates and risk overrides are applied on the base decision, and the
clarification rule only fires when the agent itself flagged missing info.
"""
from __future__ import annotations

PRIORITY_ORDER = ["P4", "P3", "P2", "P1"]


def _raise_one_level(p: str, ceiling: str = "P2") -> str:
    """Raise priority by one level toward P1, capped at `ceiling` - but NEVER
    lower an already-higher priority. The account-risk override is an *escalation*
    rule; it must not downgrade a true P1 outage to P2 just because the account is
    near renewal. So the result is max(current, min(current+1, ceiling))."""
    if p not in PRIORITY_ORDER:
        return p
    i = PRIORITY_ORDER.index(p)
    cap = PRIORITY_ORDER.index(ceiling)
    target = min(i + 1, len(PRIORITY_ORDER) - 1)   # one step up
    target = min(target, cap)                       # not past the ceiling
    target = max(target, i)                         # but never below where we are
    return PRIORITY_ORDER[target]


def _add_signal(decision: dict, text: str) -> None:
    sigs = decision.setdefault("account_signals", [])
    if text not in sigs:
        sigs.append(text)


def _trace(decision: dict, action: str, observation: str, source: str) -> None:
    """Append a policy-enforcement step with its KB source, for auditability."""
    trace = decision.setdefault("reasoning_trace", [])
    step = len(trace) + 1
    trace.append({"step": step, "action": action,
                  "observation": observation, "policy_source": source})


def apply_policy(decision: dict, evidence: dict) -> dict:
    """Apply mandatory policy to the model's proposed `decision` in place.

    `evidence` is the deterministic facts gathered by tools (not model prose):
      {
        "security_flag": bool,          # agent/classifier judged data-exposure
        "security_reason": str,
        "health": {within_90d_renewal, usage_decline_gt_25pct_3m,
                   open_escalations, tier, ...} | None,
        "duplicate_of": str | None,     # confirmed original ticket id
        "duplicate_original_priority": str | None,
        "insufficient_info": bool,      # agent could not determine module/symptom
        "injection_detected": bool,
      }
    Returns the same decision dict, corrected and with trace entries added.
    """
    # --- 1. Security / data exposure (KB-6401) - absolute, checked first ----
    if evidence.get("security_flag"):
        before = (decision.get("category"), decision.get("priority"),
                  decision.get("route_to"))
        decision["category"] = "security"
        decision["priority"] = "P1"
        decision["route_to"] = "security"
        decision["escalate"] = True
        decision["notify_tam"] = True
        reason = evidence.get("security_reason", "Suspected data exposure.")
        _add_signal(decision, "Security/data-exposure report")
        # KB-6401 mandates the reply content: acknowledge receipt, say Security is
        # engaged, do NOT speculate on cause/scope, advise pausing an affected
        # sourcing event. Overwrite any reassuring "no action needed" draft.
        decision["customer_reply"] = (
            "Thank you for reporting this. We are treating it as a priority security "
            "matter and our Security team is engaged and investigating. We will not "
            "speculate on the cause or scope until they confirm their findings. If this "
            "relates to an active sourcing event, we recommend pausing that event until "
            "the Security team confirms containment. We will keep you updated.")
        decision["internal_note"] = (
            "Potential data exposure between suppliers/tenants. Per KB-6401: classified "
            "Security/P1, routed to Security, escalated, TAM notified. Do not reproduce in "
            "the customer tenant. " + (reason or ""))
        if before != ("security", "P1", "security"):
            _trace(decision, "enforce_security_policy",
                   f"Data-exposure report -> category=security, P1, route=security, "
                   f"escalate=true, notify_tam=true (was {before}). Reason: {reason}", "KB-6401")

    # --- 5. Untrusted ticket content / injection (KB-2914) -----------------
    # Priority/routing never come from ticket text; we only record the attempt.
    if evidence.get("injection_detected"):
        _add_signal(decision, "Prompt-injection attempt in ticket text (ignored)")
        _trace(decision, "ignore_injected_instruction",
               "Instruction embedded in ticket text detected; it does not affect "
               "priority, routing, or what data is shared.", "KB-2914")

    # --- 2. Insufficient information (KB-2914) -----------------------------
    # Only when the agent itself could not determine module/symptom, AND the
    # ticket is not a security case (security already decided above).
    if evidence.get("insufficient_info") and decision.get("category") != "security":
        before = (decision.get("priority"), decision.get("needs_clarification"))
        decision["category"] = "needs_info"
        decision["priority"] = "P3"
        decision["needs_clarification"] = True
        if not decision.get("route_to"):
            decision["route_to"] = "L1_support"
        _trace(decision, "enforce_insufficient_info",
               f"Module/symptom undeterminable -> needs_info, P3, "
               f"needs_clarification=true (was {before}).", "KB-2914")

    # --- 4. Duplicate (KB-5930) --------------------------------------------
    # Keep the original ticket's handling; do not re-escalate. Security cases are
    # never downgraded to a duplicate.
    dup = evidence.get("duplicate_of")
    if dup and decision.get("category") != "security":
        decision["duplicate_of"] = dup
        decision["category"] = "duplicate"
        orig_p = evidence.get("duplicate_original_priority")
        if orig_p in PRIORITY_ORDER:
            decision["priority"] = orig_p
        decision["escalate"] = False
        _add_signal(decision, f"Duplicate of {dup}")
        _trace(decision, "enforce_duplicate_policy",
               f"Open/pending match within 7 days -> duplicate_of={dup}, "
               f"keep original priority ({orig_p}), do not re-escalate.", "KB-5930")

    # --- 3. Account-risk override (KB-2914) --------------------------------
    # Applies on top of the base decision, but NOT to duplicates (their priority
    # is pinned to the original) and NOT to security (already P1/escalated).
    health = evidence.get("health") or {}
    if (decision.get("category") not in ("security", "duplicate")
            and health.get("within_90d_renewal")
            and (health.get("usage_decline_gt_25pct_3m")
                 or (health.get("open_escalations") or 0) >= 2)):
        before = decision.get("priority")
        raised = _raise_one_level(before, ceiling="P2")
        decision["priority"] = raised
        decision["notify_tam"] = True
        decision["escalate"] = True
        why = []
        if health.get("usage_decline_gt_25pct_3m"):
            pct = health.get("active_user_change_pct_3m")
            why.append(f"active users {pct}% over 3 months")
        if (health.get("open_escalations") or 0) >= 2:
            why.append(f"{health['open_escalations']} open escalations")
        _add_signal(decision, f"Within 90 days of renewal; {', '.join(why)}")
        if raised != before:
            _trace(decision, "enforce_account_risk_override",
                   f"Renewal <90d AND ({'; '.join(why)}) -> priority {before}->{raised} "
                   f"(max P2), notify_tam=true, escalate=true.", "KB-2914")
        else:
            _trace(decision, "enforce_account_risk_override",
                   f"Renewal <90d AND ({'; '.join(why)}) -> notify_tam=true "
                   f"(priority already at/above cap).", "KB-2914")

    # --- escalate flag consistency (KB-2914) -------------------------------
    # "Set escalate = true for P1, security reports, and account risk overrides."
    # A duplicate keeps the original handling and is not re-escalated.
    if decision.get("priority") == "P1" and decision.get("category") != "duplicate" \
            and not decision.get("escalate"):
        decision["escalate"] = True
        _trace(decision, "enforce_escalate_for_p1",
               "P1 ticket -> escalate=true per escalation policy.", "KB-2914")

    return decision
