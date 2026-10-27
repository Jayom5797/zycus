"""P0 regression tests: mandatory policy must hold on every exit path and must
never downgrade a true P1. Run:  python -m pytest tests/ -q   (or python tests/run_tests.py)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tam_agent.policy import apply_policy, _raise_one_level


def _base(**kw):
    d = {"category": "how_to", "priority": "P4", "route_to": "L1_support",
         "escalate": False, "notify_tam": False, "duplicate_of": None,
         "needs_clarification": False, "account_signals": [], "reasoning_trace": []}
    d.update(kw)
    return d


# --- P0 #3: override must never lower a true P1 -----------------------------
def test_override_never_downgrades_p1():
    # True P1 outage AND account in renewal window with risk signals.
    d = apply_policy(_base(category="incident", priority="P1", escalate=True),
                     {"health": {"within_90d_renewal": True,
                                 "usage_decline_gt_25pct_3m": True,
                                 "open_escalations": 3, "active_user_change_pct_3m": -40}})
    assert d["priority"] == "P1", f"override downgraded P1 to {d['priority']}"
    assert d["notify_tam"] is True
    assert d["escalate"] is True


def test_raise_one_level_bounds():
    assert _raise_one_level("P4") == "P3"
    assert _raise_one_level("P3") == "P2"
    assert _raise_one_level("P2") == "P2"   # capped
    assert _raise_one_level("P1") == "P1"   # never lowered


# --- P0 #1/#2: security must hold even with no model evidence ---------------
def test_security_forced_from_deterministic_flag_only():
    # Simulates a provider-error fallback: no model 'security', just the
    # deterministic data-exposure flag. Must still become P1/security/escalate.
    d = apply_policy(_base(category="needs_info", priority="P3"),
                     {"security_flag": True,
                      "security_reason": "supplier saw competitor bids"})
    assert d["category"] == "security"
    assert d["priority"] == "P1"
    assert d["route_to"] == "security"
    assert d["escalate"] is True
    assert d["notify_tam"] is True


def test_security_beats_account_risk_and_duplicate():
    d = apply_policy(_base(category="how_to", priority="P4"),
                     {"security_flag": True, "duplicate_of": "TKT-999",
                      "health": {"within_90d_renewal": True, "open_escalations": 2}})
    assert d["category"] == "security"
    assert d["priority"] == "P1"
    assert d["duplicate_of"] is None   # a leak is never downgraded to a duplicate


def test_security_reply_never_leaks_and_acknowledges():
    d = apply_policy(_base(category="how_to", priority="P4",
                           customer_reply="No action required, it's just a display bug."),
                     {"security_flag": True})
    reply = d["customer_reply"].lower()
    assert "security team" in reply
    assert "no action" not in reply  # the reassuring draft must be overwritten


# --- P0 #5: incomplete execution surfaces (injection recorded, not obeyed) ---
def test_injection_recorded_not_obeyed():
    d = apply_policy(_base(category="how_to", priority="P4"),
                     {"injection_detected": True})
    assert d["priority"] == "P4"
    assert d["escalate"] is False
    assert any("njection" in s for s in d["account_signals"])


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}"); passed += 1
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
