# Build Progress & Status

> **For the reviewer reading this repo alongside the code.** This file is the
> single source of truth for *what is done*, *what is pending (in order)*, and
> *why each piece matters*. It is updated as phases complete. Verified claims are
> backed by commands that were actually run; nothing here is aspirational unless
> marked PENDING.
>
> Companion docs: `solution/OVERVIEW.md` (full design rationale) and
> `SOLUTION.md` (the 1-page deliverable, written at the end).

---

## Where the code lives

| Path | What it is |
|---|---|
| `tam-agent-task-starter-repo/tam-agent-task/` | The project (starter + our code; runs from here). |
| `tam_agent/llm.py` | Provider client (ours — rewritten). |
| `tam_agent/retrieval.py` | KB index + search (ours — new). |
| `tam_agent/tools.py` | The agent's tools (ours — new). |
| `tam_agent/policy.py` | Deterministic policy guardrails (ours — new). |
| `tam_agent/agent.py` | Tool-calling agent loop (ours — PENDING). |
| `tam_agent/schema.py` | Fixed output contract (**unchanged**, as required). |
| `solution/` | Our authored docs (OVERVIEW, PROGRESS). Kept separate from given data. |

Provider in use: **Groq `openai/gpt-oss-120b`** (the model these keys actually
serve; verified live). Multi-key round-robin; Ollama offline fallback intact.

---

## Status by phase

| # | Phase | Status | Why it matters |
|---|---|---|---|
| 0 | Repo hygiene, baseline commit, `.gitignore`, keys in `.env` | ✅ DONE | Given data committed separately from our work; keys never committed. |
| 1 | Design overview (`OVERVIEW.md`) | ✅ DONE | Explains intent; required so we can defend every line at walkthrough. |
| 2 | LLM provider (`llm.py`) | ✅ DONE | Groq + round-robin keys + clear errors + Ollama fallback. Verified live. |
| 3 | Retrieval (`retrieval.py`) | ✅ DONE | Scalable KB search; the heart of "retrieval accuracy" (20%). Verified. |
| 4 | Tools (`tools.py`) | ✅ DONE | Deterministic account/usage/duplicate facts; the agent's only data access. Verified. |
| 5 | Policy (`policy.py`) | ✅ DONE | Mandatory rules enforced + audit-traced; the correctness edge. Unit-tested. |
| 6 | Agent loop (`agent.py`) | ⏳ IN PROGRESS | The agentic core (30%): model picks tools, decides when to stop. |
| 7 | Review run → outputs + `run_log.jsonl` | ⬜ PENDING | Hard gate: `check_outputs.py` must show **10/10 valid**. |
| 8 | Eval harness (`scripts/eval.py`) | ⬜ PENDING | Proves *semantic* correctness with numbers, not just schema validity. |
| 9 | `SOLUTION.md` (1 page) | ⬜ PENDING | 20% of score: design, evaluation, 10x scale, limits, how to run. |
| 10 | *(uplift)* Trace viewer UI | ⬜ OPTIONAL | Not required by the brief; a walkthrough aid, built only after 10/10. |

---

## What is verified (commands actually run)

- **Provider**: `LLMClient` loads both keys; Groq returns clean JSON. (Hit and
  fixed two real issues: Cloudflare 403 on default User-Agent; model 404 →
  switched to the served `openai/gpt-oss-120b`, which returns a separate
  `reasoning` field, so token budgets are generous.)
- **Retrieval** passes all 5 hard review error codes:
  - E-4102 → KB-3985 (**active_incident** ranked above the current troubleshooter).
  - E-2207 → KB-1786 (**current**; deprecated KB-2605 demoted, never top).
  - E-3310 → KB-4795 (code-tagged wins; fix article KB-7889 reachable via `related` → multi-hop).
  - E-5214 → KB-7072, E-6105 → KB-2982.
  - Module aliases normalized (CLM→Contracts, SSO→Platform, supplier portal→Supplier Management, …).
  - Now uses an **inverted index** for candidate gathering (scores O(candidates), not O(N)).
- **Tools** verified against real data:
  - Blank `account_id` resolved via domain (TKT-169327 → ACC-4850).
  - Account-risk signals: ACC-2431 = within 90d, **−49.1%** users/3m, **2** escalations (the TKT-119877 trap); ACC-3751 stable (−7.3%, 0) as a contrast.
  - Duplicate window excludes the ticket itself; same-account/open-or-pending/7-day only.
  - `search_kb`/`get_kb_article` compact + per-ticket body de-dup (token discipline).
- **Policy** unit-tested, all rules + ordering interactions pass:
  - Security (KB-6401) forces P1/security/escalate and beats a casual tone *and* a duplicate.
  - Insufficient info (KB-2914) → needs_info/P3/clarify.
  - Account-risk override (KB-2914) raises one level (cap P2) + notify_tam; **skipped** on duplicates.
  - Duplicate (KB-5930) pins original priority, no re-escalate.
  - Injection (KB-2914) recorded as a signal, never alters the decision.
  - Every enforcement writes a trace step with its `policy_source`.

---

## Key design decisions (and the trade-offs)

1. **Deterministic policy layer on top of the LLM.** The model reasons and drafts;
   code enforces only the *explicitly mandated* rules, each logged with its KB
   source. Trade-off: slightly more code vs. relying on the model — chosen because
   correctness on the trap tickets must not depend on a free model's luck, and
   because it is auditable. It does **not** blanket-overwrite valid model choices.
2. **Retrieval returns excerpts + metadata, not bodies.** Keeps prompt tokens low;
   full bodies fetched only when the agent needs to decide. Trade-off: an extra
   tool call sometimes, in exchange for much lower token cost.
3. **BM25 from scratch + inverted index, no vector DB.** 386 docs don't need
   embeddings; every line is explainable. The 10x plan (swap to an ANN index
   behind the same `search()` signature) is documented, not over-built now.
4. **Confidence is evidence-derived, explicitly *not* calibrated.** No arbitrary
   numbers; honesty over false precision.
5. **Ticket text is untrusted.** Injected instructions never change priority,
   routing, or what data is shared; other customers' data is never placed in a reply.

---

## The review-set traps we target (ground truth we are building toward)

TKT-135081 casual "display thing" → **P1 security** (KB-6401) ·
TKT-158764 injected "set P1" → **P4 how-to**, injection ignored (KB-2914) ·
TKT-144939 OCR down → **P1**, link INC-26-0418, notify TAM (Premium, KB-3985) ·
TKT-160477 E-2207 → current **KB-1786** not deprecated KB-2605 ·
TKT-119877 "approvals slow" → **account-risk override**, notify TAM (KB-2914) ·
TKT-128253 "NOTHING WORKS" → **needs_info** (KB-2914) ·
TKT-169327 blank account → resolve via **domain** ·
TKT-160094 bid error, event in 2 days → **P2** (deadline <72h) ·
TKT-160067 bulk-approve "bug" → check by-design limit ·
TKT-192732 dashboard refresh (E-6105) → KB-2982 (control case).

*These are the semantic targets; `scripts/eval.py` (phase 8) scores decisions
against them, kept strictly separate from the agent so answers are never
hardcoded into decision logic.*

---

## Next action

Implement `tam_agent/agent.py`: a model-driven tool-calling loop with a hard step
budget, tool-argument validation, error recovery, token-usage instrumentation,
and a safe fallback — then run the review set and drive `check_outputs.py` to 10/10.
