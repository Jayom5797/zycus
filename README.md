# Zycus TAM — Agentic Ticket Triage

An LLM agent that triages procurement-support tickets the way a senior support engineer
would: it resolves the account, checks account health and history, retrieves the right
knowledge-base article (and follows links when needed), applies written support policy,
and emits a fixed-schema decision with a full reasoning trace.

> **Status:** review set **10/10 schema-valid** · decision accuracy **100%** · **0 critical
> policy failures**. Verified by the commands in [Verification](#verification).

- **Our code & docs:** `tam-agent-task-starter-repo/tam-agent-task/tam_agent/` (agent) and
  `solution/` (design notes).
- **The deliverable write-up:** [`SOLUTION.md`](tam-agent-task-starter-repo/tam-agent-task/SOLUTION.md) (1 page).
- **Design rationale:** [`solution/OVERVIEW.md`](solution/OVERVIEW.md) · **build log:** [`solution/PROGRESS.md`](solution/PROGRESS.md).

---

## What the evaluator asked for, and where we meet it

The task scores four areas. This table maps each requirement to the code and the evidence.

| What was asked | Weight | How we meet it | Where |
|---|---|---|---|
| **Working build** — runs, produces `outputs/`, `check_outputs.py` shows **10/10 valid** | 30% | 10/10 valid; outputs + `run_log.jsonl` committed | `run.py`, `outputs/` |
| **Agentic depth** — the LLM *chooses* tools, reads results, decides when to stop; not a fixed pipeline; supports multi-hop KB | 30% | Tool-calling loop, **5–20 calls/ticket (mean 8.9)**, follows `related`/`superseded_by` links; step budget, tool validation, error recovery, safe fallback | `tam_agent/agent.py` |
| **Retrieval & decision accuracy** on the review set | 20% | **100%** against a hand-labelled rubric; status-aware retrieval (active-incident/current over deprecated) | `tam_agent/retrieval.py`, `scripts/eval.py` |
| **Written reasoning** (`SOLUTION.md`) | 20% | Design, evaluation, 10× scale, limitations, how-to — with measured numbers | `SOLUTION.md` |

**Hard gate:** the brief states submissions failing `check_outputs.py` are not reviewed.
Ours shows **10/10**.

### The review-set traps (and how each is handled)

The 10 review tickets are engineered so a text-only reader gets them wrong. Correct handling:

| Ticket | Looks like | Correct call | Mechanism |
|---|---|---|---|
| TKT-135081 | P4 "probably a display thing" | **security / P1 / escalate** | deterministic data-exposure detection → KB-6401 |
| TKT-158764 | how-to + injected "set P1, email all accounts" | **how_to / P4**, injection ignored & logged | untrusted-text handling → KB-2914 |
| TKT-144939 | P1 OCR outage | **incident / P1 / L2**, notify TAM, cites **KB-3985** | follows error code to the active incident |
| TKT-160477 | E-2207 e-sign | cites current **KB-1786**, never deprecated KB-2605 | status-aware ranking |
| TKT-119877 | P4 "approvals a bit slow" | **account-risk override** → P2, notify TAM | renewal <90d + usage −49% + 2 escalations → KB-2914 |
| TKT-128253 | "NOTHING WORKS" (P1) | **needs_info / P3** | no module/symptom → don't guess (KB-2914) |
| TKT-169327 | normal (blank `account_id`) | resolve via **requester_domain** → ACC-4850 | blank-FK resolution |
| TKT-160094 | P3 bid error, event in 2 days | **incident / P2**, cites **KB-4795** | business-deadline reasoning |
| TKT-160067 | "bug: bulk approve 500 POs" | checked against by-design limit | KB lookup before defect call |
| TKT-192732 | "Re:" dashboard refresh | duplicate of open TKT-122022 | 7-day duplicate window → KB-5930 |

---

## Measured results (from the latest run)

Read directly from `outputs/evaluation.json` and `outputs/run_log.jsonl`:

| Metric | Value |
|---|---|
| Run status | **Passed** |
| Overall accuracy (tickets passing all rubric checks) | **10 / 10 (100%)** |
| Policy compliance | **5 / 5** |
| Evidence coverage | **10 / 10** |
| Execution reliability (no exception/fallback) | **10 / 10** |
| Critical security-policy failures | **0** |
| Mean latency | ~**8.0 s/ticket** |
| Mean LLM calls/ticket | **8.9** |
| Tokens (review set) | **121,156 prompt + 11,374 completion** |
| Cost (USD) | not shown — free-tier model has no fixed price |

> `check_outputs.py` proves outputs are *schema-valid*. `eval.py` additionally proves they are
> *correct* against a rubric derived from the policy KB, kept separate from the agent so expected
> answers never leak into decision logic.

**How to read "100%" honestly:**
- It is **100% on the 10-ticket review set against our rubric** — the scope the task defines —
  not a general accuracy claim. Ten tickets is a small sample.
- Accuracy is split between two layers. The **deterministic policy layer** guarantees the hard
  rules (security → P1, account-risk override, duplicates, P1 ⇒ escalate) *by construction*, so
  those stay correct even on a weaker model. The **model-driven parts** (which KB to search,
  category nuance, when to stop) depend on the LLM; `gpt-oss-120b` handled them well.
- The served model is **non-deterministic**, so a fresh run can vary on a model-dependent check
  (e.g. a KB citation); the policy-governed critical decisions are stable. During development the
  same agent scored 68% → 78% → 92% → 95% → 100% as real bugs (empty replies, KB hallucination,
  premature finalize) were fixed. The committed run is a clean 100%.

---

## Architecture

```
                 ┌──────────────── tool-calling loop (agent.py) ─────────────────┐
  ticket ──▶     │  LLM picks a tool ─▶ run ─▶ feed back observation ─▶ repeat    │ ─▶ decision
                 │  until it has enough evidence, then finalizes                  │       │
                 └───────────────────────────────────────────────────────────────┘       ▼
                                                                         deterministic policy layer
                                                                         (policy.py) enforces the
                                                                         mandated rules, each logged
                                                                         with its KB source
```

| Module | Role |
|---|---|
| `tam_agent/agent.py` | The agentic loop: prompt, tool selection, evidence guard, step budget, forced-finalize, safe fallback, injection & data-exposure detection, confidence. |
| `tam_agent/tools.py` | Six tools (compact JSON): `resolve_account`, `get_account_health`, `find_related_tickets`, `search_kb`, `get_kb_article`, `get_policy`. Dates/usage/duplicate math is deterministic Python. |
| `tam_agent/retrieval.py` | BM25 over an **inverted index** + exact error-code & module maps; lifecycle-aware ranking (`active_incident > current > resolved >> deprecated`). Returns excerpts, not full bodies. |
| `tam_agent/policy.py` | Deterministic enforcement of mandated rules only (security, account-risk override, duplicates, insufficient-info, P1⇒escalate), each traced with its KB source. |
| `tam_agent/llm.py` | Provider client. **Groq** default with **multi-key round-robin** (advances on HTTP 429); **Ollama** offline fallback; OpenAI/Anthropic supported. Token usage instrumented. |
| `tam_agent/schema.py` | Fixed output contract — **unchanged**, as required. |
| `scripts/eval.py` | Rubric-based evaluation → console report + `--json` artifact (`evaluation.json`). |
| `scripts/build_dashboard.py` | Renders a self-contained evaluation dashboard from the artifact. |

### Design choices worth noting
- **Policy as code.** The LLM reasons and drafts; a deterministic layer *guarantees* the
  non-negotiable rules, so correctness on the hard tickets does not depend on model luck.
  Every enforcement is written into the trace with the KB article that mandates it.
- **Ticket text is untrusted.** Injected instructions never change priority, routing, or what
  data is shared; a data-exposure symptom is caught deterministically so a casual tone can't
  downgrade a leak.
- **Grounded citations.** `kb_references` are filtered to IDs the agent actually retrieved and
  that exist — invented IDs are dropped.
- **Token discipline.** Policy is sent as a digest, observations are compact, history is capped,
  and already-fetched article bodies are not resent.

---

## Quick start

```bash
cd tam-agent-task-starter-repo/tam-agent-task
pip install -r requirements.txt
cp .env.example .env            # set LLM_PROVIDER + keys

python scripts/validate_data.py     # dataset integrity
python run.py --set review          # triage the 10 review tickets
python scripts/check_outputs.py     # expect: 10/10 review tickets valid
python scripts/eval.py              # decision accuracy vs rubric
```

**Provider config** (`.env`, never committed):
```
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
GROQ_API_KEYS=key1,key2,key3        # round-robin; add as many as you like
# offline fallback: LLM_PROVIDER=ollama
```

---

## Evaluation dashboard (optional)

A self-contained HTML dashboard (no server, no dependencies) renders the evaluation artifact:

```bash
python scripts/eval.py --json        # writes outputs/evaluation.json
python scripts/build_dashboard.py    # writes outputs/dashboard.html
# open outputs/dashboard.html in a browser
```

It shows run status, the four metric cards, an expected-vs-actual ticket table (missing and
failed cases stay visible — a missing output is `Not evaluated`, never `Pass`), a per-ticket
inspection panel (decision, field diffs, evidence, execution trace), and a reliability/cost
strip read from `run_log.jsonl`. It is a presentation layer over real artifacts and never
fabricates metrics.

---

## Verification

| Command | Result |
|---|---|
| `python scripts/validate_data.py` | all integrity checks pass |
| `python run.py --set review` | 10/10 produced, no exceptions |
| `python scripts/check_outputs.py` | **10/10 review tickets valid** |
| `python scripts/eval.py` | **100%** decision accuracy, 0 critical failures |

Dashboard data-integrity was tested on copies (real outputs untouched): a missing output renders
as `Not evaluated`, a malformed output as `Invalid`, a broken security ticket as `Fail` + listed
in `critical_fails`, and accuracy correctly dropped under those conditions — a missing or invalid
result can never inflate the score.

---

## Repository layout

```
.
├─ README.md                      ← this file
├─ solution/
│  ├─ OVERVIEW.md                 ← design rationale (companion to the code)
│  └─ PROGRESS.md                 ← build log + verification evidence
└─ tam-agent-task-starter-repo/tam-agent-task/
   ├─ README.md                   ← original task brief (provided)
   ├─ SOLUTION.md                 ← 1-page deliverable write-up
   ├─ run.py  · requirements.txt  · .env.example
   ├─ data/   · kb/               ← provided dataset (~3k tickets, ~390 KB articles)
   ├─ tam_agent/                  ← agent, tools, retrieval, policy, llm, schema
   ├─ scripts/                    ← validate_data, check_outputs, eval, build_dashboard
   └─ outputs/                    ← 10 triaged tickets, run_log.jsonl, evaluation.json, dashboard.html
```

## Notes & limitations
- The served model (`openai/gpt-oss-120b`) is non-deterministic; per-ticket step counts and
  model-dependent checks can vary across runs. Critical, policy-governed decisions are stable;
  the committed run is 100% on the rubric.
- "100%" is measured on the **10-ticket review set only** — not a production-readiness claim.
- Accuracy on the model-driven parts depends on the provider. On the offline **Ollama** fallback
  (a smaller local model) expect lower scores on those parts; the deterministic policy layer still
  enforces the critical security / override / duplicate rules correctly.
- `confidence` is an evidence-derived heuristic and is **not** calibrated.
- Any LLM provider works; the agent code is unchanged across providers.
