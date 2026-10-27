"""P0 #2 regression: on provider error / empty / malformed / budget-exhaustion,
a data-exposure ticket must STILL be forced to security/P1 - never a bland P3/L1.

Uses a stub LLM so no network/keys are needed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tam_agent.data import load_accounts, load_tickets, load_usage
from tam_agent.kb import load_kb
from tam_agent import agent as A
from tam_agent.agent import Context, triage
from tam_agent.llm import LLMError

# The known data-exposure review ticket (supplier saw competitor bids).
SEC_TID = "TKT-135081"


def _ctx():
    return Context(load_accounts(), load_tickets(), load_usage(), load_kb())


def _sec_ticket(ctx):
    return ctx.tickets.set_index("ticket_id").loc[SEC_TID].to_dict() | {"ticket_id": SEC_TID}


class StubLLM:
    """Replaces ctx.llm with a scripted failure mode."""
    def __init__(self, mode):
        self.mode = mode
        self.calls = 0
        self.prompt_tokens = self.completion_tokens = 0
        self.last_usage = None

    def chat(self, messages, system=None, max_tokens=1024, temperature=0.0, reasoning_effort=None):
        self.calls += 1
        if self.mode == "error":
            raise LLMError("simulated provider outage")
        if self.mode == "empty":
            return ""                       # reasoning-only empty content, forever
        if self.mode == "malformed":
            return "I think this is fine, no JSON here at all."
        if self.mode == "loop_tools":
            # always ask for a tool, never finalize -> exhaust the step budget
            return '{"thought":"look","tool":"get_policy","args":{}}'
        return ""


def _assert_secure(out, mode):
    assert out["category"] == "security", f"[{mode}] category={out['category']} (expected security)"
    assert out["priority"] == "P1", f"[{mode}] priority={out['priority']} (expected P1)"
    assert out["route_to"] == "security", f"[{mode}] route={out['route_to']}"
    assert out["escalate"] is True, f"[{mode}] escalate={out['escalate']}"
    # must NOT be the bland fallback
    assert not (out["category"] == "needs_info" and out["route_to"] == "L1_support"), \
        f"[{mode}] fell back to bland P3/L1 on a data-exposure ticket"


def _run(mode):
    ctx = _ctx()
    ctx.llm = StubLLM(mode)
    t = _sec_ticket(ctx)
    return triage(t, ctx)


def test_security_holds_on_provider_error():
    _assert_secure(_run("error"), "error")


def test_security_holds_on_empty_replies():
    _assert_secure(_run("empty"), "empty")


def test_security_holds_on_malformed_json():
    _assert_secure(_run("malformed"), "malformed")


def test_security_holds_on_budget_exhaustion():
    _assert_secure(_run("loop_tools"), "loop_tools")


def test_output_is_schema_valid_on_failpath():
    from tam_agent.schema import validate
    out = _run("error")
    assert validate(out) == [], f"schema errors on failpath: {validate(out)}"


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
