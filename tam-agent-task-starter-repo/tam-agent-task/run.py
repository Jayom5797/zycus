"""Run the triage agent.

  python run.py --set review            # the 10 review tickets (required)
  python run.py --set sample --n 50     # random sample (optional)
  python run.py --ids TKT-123456 TKT-234567

Writes one JSON per ticket to outputs/ plus outputs/run_log.jsonl.
"""
import argparse, json, random, time, traceback
from pathlib import Path

from tam_agent.data import load_accounts, load_tickets, load_usage, load_review_set
from tam_agent.kb import load_kb
from tam_agent.agent import Context, triage
from tam_agent.schema import validate

OUT = Path(__file__).resolve().parent / "outputs"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["review", "sample"], default=None)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ids", nargs="*")
    args = ap.parse_args()

    tickets = load_tickets()
    ctx = Context(load_accounts(), tickets, load_usage(), load_kb())
    if args.ids:
        ids = args.ids
    elif args.set == "sample":
        ids = random.Random(args.seed).sample(list(tickets["ticket_id"]), args.n)
    else:
        ids = load_review_set()

    OUT.mkdir(exist_ok=True)
    by_id = tickets.set_index("ticket_id")
    ok = 0
    tot_prompt = tot_completion = 0
    with open(OUT / "run_log.jsonl", "a") as log:
        for tid in ids:
            t0 = time.time()
            calls0 = ctx.llm.calls
            pt0, ct0 = ctx.llm.prompt_tokens, ctx.llm.completion_tokens
            rec = {"ticket_id": tid}
            try:
                ticket = by_id.loc[tid].to_dict() | {"ticket_id": tid}
                result = triage(ticket, ctx)
                errs = validate(result)
                (OUT / f"{tid}.json").write_text(json.dumps(result, indent=2))
                rec |= {"status": "ok" if not errs else "schema_error", "errors": errs,
                        "category": result.get("category"), "priority": result.get("priority"),
                        "route_to": result.get("route_to")}
                ok += not errs
            except Exception as e:
                rec |= {"status": "exception", "errors": [repr(e)], "traceback": traceback.format_exc(limit=3)}
            # token usage is captured when the provider reports it (guardrail S3).
            dp = ctx.llm.prompt_tokens - pt0
            dc = ctx.llm.completion_tokens - ct0
            tot_prompt += dp
            tot_completion += dc
            rec |= {"seconds": round(time.time() - t0, 2),
                    "llm_calls": ctx.llm.calls - calls0,
                    "prompt_tokens": dp, "completion_tokens": dc}
            log.write(json.dumps(rec) + "\n")
            print(f"{tid}: {rec['status']} ({rec['seconds']}s, {rec['llm_calls']} calls, "
                  f"{dp}+{dc} tokens)")
    n = len(ids)
    print(f"\n{ok}/{n} tickets produced valid output")
    if tot_prompt or tot_completion:
        print(f"Tokens: total {tot_prompt + tot_completion} "
              f"(prompt {tot_prompt}, completion {tot_completion}); "
              f"mean/ticket {(tot_prompt + tot_completion) // max(n, 1)}")
    else:
        print("Tokens: not reported by this provider.")


if __name__ == "__main__":
    main()
