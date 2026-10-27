# SOLUTION — Agentic Ticket Triage

## 1. Design

The agent is a **tool-calling loop** (`tam_agent/agent.py`). Each turn the LLM returns one
JSON object that is either a tool call or a final decision; tool results are fed back and it
decides the next step. The number of steps is driven by the ticket, not scripted — the review
set ran **5–20 LLM calls per ticket (mean 8.9)**: one lookup for a blank-account resolution,
more for a ticket that follows a KB link to an active incident.

**Tools** (`tam_agent/tools.py`), all returning compact JSON:
`get_policy` (digest of the 3 policy articles) · `resolve_account` (by id, or by
`requester_domain` when the id is blank) · `get_account_health` (renewal window + 3-month
usage trend + escalations, computed in Python) · `find_related_tickets` (7-day duplicate
window) · `search_kb` · `get_kb_article` (follow `related` / `superseded_by` links).

**Retrieval** (`tam_agent/retrieval.py`): BM25 over an **inverted index** (scores only
candidate docs, not all 386), plus exact error-code and module maps. Ranking is lifecycle-aware
(`active_incident > current > resolved >> deprecated`), so E-4102 surfaces the active incident
KB-3985 and E-2207 surfaces the current KB-1786, never the deprecated KB-2605. Returns headers +
short excerpts; full bodies only on demand.

**When it stops:** the model finalizes once it has the evidence. An **evidence guard** blocks a
premature finalize until the account is resolved, health is checked, and (for coded tickets) the
KB is searched — it nudges the model to call the tool; it never fabricates the evidence. A hard
budget (12 steps) with a forced-finalize and a safe fallback guarantees a valid result.

**Policy layer** (`tam_agent/policy.py`): after the model proposes, deterministic code enforces
only the *mandated* rules, each logged in the trace with its KB source — security/data-exposure
→ P1/security/escalate (KB-6401), account-risk override (KB-2914), duplicate handling (KB-5930),
insufficient-info, and P1⇒escalate. Ticket text is treated as untrusted: an injected
"set P1 / email all accounts" is detected, ignored, and recorded; data-exposure is detected
deterministically so a casual tone cannot downgrade a leak.

## 2. Evaluation

`scripts/eval.py` scores decisions against a hand-labelled rubric, kept separate from the agent
so answers never leak into decision logic. Measured on the review set: **10/10 schema-valid
(`check_outputs.py`), 37/37 rubric checks (100%)**. To put this in production I would measure:

- Per-field accuracy (category, priority, routing, escalation, TAM, duplicate, account
  resolution, KB selection, clarification) against a larger human-labelled set, not just 10.
- **Security recall** specifically — false negatives on data exposure are the costliest error.
- Injection-safety rate (no priority/routing change, no cross-customer data in replies).
- Retrieval quality: precision@k and whether a deprecated article is ever cited over a current one.
- Calibration of `confidence` against observed correctness (currently evidence-derived, **not
  calibrated**).
- Human escalation rate / agreement in a shadow-mode trial before auto-actioning.

## 3. Scale (10× → ~30k tickets, ~4k KB articles)

- **Retrieval:** the inverted index already avoids full scans, but at 4k articles keyword recall
  degrades. I'd swap BM25 for an embedding + ANN index (e.g. FAISS) **behind the same
  `search_kb` signature** — the agent code is unchanged. The index builds once per run.
- **Cost/latency:** cost is dominated by LLM calls, not data size. The run used **mean ~13.3k
  tokens/ticket** (121k prompt + 11k completion across 10). Policy is sent as a digest and
  history is capped, so prompt size is roughly flat as the KB grows. Latency was ~8s/ticket mean;
  the multi-account round-robin key pool (advancing on HTTP 429) is what makes a batch run
  sustainable under free-tier TPM limits.
- **What I'd change:** cache account-health and policy per run; cap `search_kb` candidates;
  consider a cheaper model for routine tickets and reserve the larger model for ambiguous ones;
  batch-embed the KB offline.

## 4. Limitations

- The hosted reasoning model (`gpt-oss-120b`) is non-deterministic and occasionally returns
  empty content; the loop absorbs this (retry + forced-finalize), but step counts vary run to run.
- Duplicate-original priority isn't re-fetched, so a duplicate keeps a reasonable default rather
  than the exact original priority.
- Data-exposure detection is pattern-based; novel phrasings could be missed (mitigated by the
  model's own `security` flag as a second path).
- `confidence` is a documented heuristic, not calibrated.
- Evaluated on the 10 review tickets only; broader accuracy is unmeasured.

## 5. How to run

```bash
pip install -r requirements.txt
cp .env.example .env            # set LLM_PROVIDER + keys (GROQ_API_KEYS=k1,k2,...)
python scripts/validate_data.py
python run.py --set review
python scripts/check_outputs.py # expect 10/10 valid
python scripts/eval.py          # decision accuracy vs rubric
```
Provider is swappable via `LLM_PROVIDER` (groq default; ollama for offline) with no code change.
