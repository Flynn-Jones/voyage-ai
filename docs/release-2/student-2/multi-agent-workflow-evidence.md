# Multi-Agent workflow evidence: Accommodation (student-2)

`accommodation_shortlist` run live on 2026-10-21, 02:03-02:04 UTC, against: real
`accommodation-be`/`accommodation-db` containers (AI enabled, not the CI override), the real
shared Multi-Agent Server (`:7004`) and MCP server (`:7001`) running on the host, and real Ollama
(`qwen2.5:7b`) — not mocked. Every request went through `accommodation-be`'s own
`/accommodation/multi-agent/*` routes (`routes/multi_agent_mode.py`), not curled against the
Multi-Agent Server directly, so this also proves the new backend wiring (services/multi_agent_api.py)
works end to end. Full request/response bodies: [`scenario-transcripts/multi-agent-scenarios.txt`](scenario-transcripts/multi-agent-scenarios.txt).
Raw `runs.db`/`audit.jsonl` excerpts for both runs: [`audit-excerpts.jsonl`](audit-excerpts.jsonl).

**Independently verified by the shared agentic_loop's `multi-agent-review` mode**, which reads
these same runs/audit entries via its own HTTP + log collector (not hand-checked): verdict
**PASS**, 16/16 checks (lifecycle order, human review recorded, tool allowlist, latency,
fallback rate) — see
[`../agentic-loop/multi-agent-review-20261021T025922Z.md`](../agentic-loop/multi-agent-review-20261021T025922Z.md)
and its matching validation log
[`../agentic-loop/multi-agent-review-validation-20261021T025922Z.jsonl`](../agentic-loop/multi-agent-review-validation-20261021T025922Z.jsonl).

## Scenario 1 — shortlist found, human partially approves (terminal)

- **run_id**: `run-0417a2081db2` · **requester**: `accommodation-be` · **final state**: `applied`
- **Input**: `{"destination": "Tokyo", "budget_cap": 150, "interests": ["pool"]}`
- **Plan** (Planner, LLM, 8779 ms): one step — `search_accommodations`, reason "To find
  pool-equipped accommodations within the budget cap of $150.00/night in Tokyo".
- **Shortlist** (Worker, deterministic Python, 152 ms, 2× `search_accommodations` calls — the
  amenities filter matched nothing, so it relaxed and retried on budget alone):

  | id | name | $/night | rating | matched interests | within budget | score |
  |---|---|---|---|---|---|---|
  | 1 | Nui. Hostel & Bar Lounge | 38.00 | 4.6 | — | yes | 4.6 |
  | 2 | Shinjuku Granbell Hotel | 145.00 | 4.3 | — | yes | 4.3 |
  | 3 | Asakusa Traditional Guesthouse | 65.00 | 4.2 | — | yes | 4.2 |

  Computed check: `weak_match` — the top pick (by score) matched none of the requested interests.
- **Reviewer** (LLM, 6573 ms): risk `medium`; finding restates the computed check verbatim;
  recommendation "Exclude 'Nui. Hostel & Bar Lounge' from the shortlist as it does not meet the
  interest in a pool." — stays in scope (narrows the shortlist, never names a booking action).
- **Human decision**: `partial` — approved ids `[2, 3]` (the hotel and guesthouse), rejected `[1]`
  (the hostel the Reviewer flagged), note "skip the hostel, prefer the pool-adjacent options".
- **Applied result**: `state: applied`, `applied_recorded: true`, `created_ids: []`,
  `target: "none (read-only workflow)"` — accommodation_shortlist never writes to
  accommodation-db, so "applied" here means the run's lifecycle was closed out, not that a row
  was created.

### History (`GET /accommodation/multi-agent/runs/run-0417a2081db2/history`)

| seq | step | agent | state | detail |
|---|---|---|---|---|
| 6 | created | — | running | requested_by: accommodation-be |
| 7 | plan | planner | running | source: llm, duration 8779ms |
| 8 | work | worker | running | source: workflow, duration 152ms, tools: search_accommodations ×2 |
| 9 | review | reviewer | running | source: llm, duration 6573ms |
| 10 | human_review | human | awaiting_human_review | — |
| 11 | human_review | human | partially_accepted | decision: partial, approved [2,3], rejected [1] |
| 12 | applied | backend | applied | created_ids: [], target: none (read-only workflow) |

### audit.jsonl excerpt

```json
{"ts": "2026-10-21T02:03:10.015+00:00", "run_id": "run-0417a2081db2", "workflow": "accommodation_shortlist", "step": "plan", "agent": "planner", "duration_ms": 8779, "source": "llm"}
{"ts": "2026-10-21T02:03:10.170+00:00", "run_id": "run-0417a2081db2", "workflow": "accommodation_shortlist", "step": "work", "agent": "worker", "duration_ms": 152, "source": "workflow"}
{"ts": "2026-10-21T02:03:16.747+00:00", "run_id": "run-0417a2081db2", "workflow": "accommodation_shortlist", "step": "review", "agent": "reviewer", "duration_ms": 6573, "source": "llm"}
{"ts": "2026-10-21T02:03:24.647+00:00", "run_id": "run-0417a2081db2", "workflow": "accommodation_shortlist", "step": "human_review", "agent": "human", "duration_ms": 0, "source": null}
{"ts": "2026-10-21T02:03:24.659+00:00", "run_id": "run-0417a2081db2", "workflow": "accommodation_shortlist", "step": "applied", "agent": "backend", "duration_ms": 0, "source": null}
```

## Scenario 2 — no candidates match the cap, human rejects (terminal)

- **run_id**: `run-18199a48477d` · **requester**: `accommodation-be` · **final state**: `rejected`
- **Input**: `{"destination": "Tokyo", "budget_cap": 20}`
- **Plan** (Planner, LLM, 6563 ms): one step — `search_accommodations`.
- **Shortlist**: zero candidates (nothing in Tokyo is <= $20/night). Computed check:
  `no_candidates`.
- **Reviewer** (LLM, 3534 ms): risk `low`; finding and recommendation are **exactly** the
  deterministic `fallback_review()` text — proof that `validate_review`'s grounding (findings
  always come from the computed checks, not the LLM; a `no_candidates` run drops every
  LLM-proposed recommendation) holds against a real model response, not only the mocked unit
  tests in `ai-services/multi-agent-server/tests/test_accommodation_shortlist.py`.
- **Human decision**: `reject`, note "too far over budget, will widen cap instead".
- **Applied result**: a rejected run is never reported to `/applied` — `applied_recorded` stays
  `null`, final state is `rejected`.

### History (`GET /accommodation/multi-agent/runs/run-18199a48477d/history`)

| seq | step | agent | state | detail |
|---|---|---|---|---|
| 18 | created | — | running | requested_by: accommodation-be |
| 19 | plan | planner | running | source: llm, duration 6563ms |
| 20 | work | worker | running | source: workflow, duration 61ms, tools: search_accommodations |
| 21 | review | reviewer | running | source: llm, duration 3534ms |
| 22 | human_review | human | awaiting_human_review | — |
| 23 | human_review | human | rejected | decision: reject, approved [], rejected [] |

### audit.jsonl excerpt

```json
{"ts": "2026-10-21T02:04:04.323+00:00", "run_id": "run-18199a48477d", "step": "plan", "agent": "planner", "duration_ms": 6563, "source": "llm"}
{"ts": "2026-10-21T02:04:04.388+00:00", "run_id": "run-18199a48477d", "step": "work", "agent": "worker", "duration_ms": 61, "source": "workflow"}
{"ts": "2026-10-21T02:04:07.924+00:00", "run_id": "run-18199a48477d", "step": "review", "agent": "reviewer", "duration_ms": 3534, "source": "llm"}
{"ts": "2026-10-21T02:04:08.078+00:00", "run_id": "run-18199a48477d", "step": "human_review", "agent": "human", "duration_ms": 0, "source": null}
```

## Scenario 3 — AI-mode / MCP / RAG / Multi-Agent all disabled (cloud default)

With `accommodation-service` started via `docker-compose.yml` + `docker-compose.ci.yml`
(`AI_ENABLED=MCP_ENABLED=RAG_ENABLED=MULTI_AGENT_ENABLED=false`), every AI-shaped route answers
**403** before doing any work: `/accommodation/mcp/by-destination`, `/accommodation/rag/refresh`,
`/ai/recommend`, `/accommodation/multi-agent/run`. Full transcript:
[`scenario-transcripts/verify-ai-disabled.txt`](scenario-transcripts/verify-ai-disabled.txt).
This is the same check `.github/workflows/student-2.yml`'s `verify-ai-disabled` job runs on every
push — see [`post-commit-cicd-report.md`](post-commit-cicd-report.md).

## Unit test coverage (fallback paths, scope boundaries, mocked MCP/LLM)

The live scenarios above exercise the happy path against a real model and real data; the full
matrix of edge cases — invalid input, a down MCP tool, the amenities-filter relaxation, an
LLM plan that tries the write tool, an LLM review that recommends a booking (rejected), risk
never dropping below the computed level — is covered deterministically in
`ai-services/multi-agent-server/tests/test_accommodation_shortlist.py` (23 tests, all passing;
[`junit-mas-accommodation-shortlist.xml`](junit-mas-accommodation-shortlist.xml)).
