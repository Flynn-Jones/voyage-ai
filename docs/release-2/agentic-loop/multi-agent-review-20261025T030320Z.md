# Multi-Agent Review

- Generated: 20261025T030320Z (UTC) by `ai-services/agentic_loop` mode `multi-agent-review`
- **Verdict: PASS** (pass 30, warn 1, gap 0, fail 0)

## Evidence consulted

- `http://localhost:7004/runs?limit=200`
- `http://localhost:7004/workflows`
- `/Users/flynnjones/Developer/voyage-ai/ai-services/multi-agent-server/logs/audit.jsonl`

## Checks

| Status | Subject | Check | Detail |
|---|---|---|---|
| PASS | destination_recommend/run-9f028d16fe0c | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-9f028d16fe0c | `human_review` | human decision recorded (applied) |
| PASS | destination_recommend/run-9f028d16fe0c | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-820601d6a716 | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-820601d6a716 | `human_review` | human decision recorded (rejected) |
| PASS | destination_recommend/run-820601d6a716 | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-70a75ccefc5c | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-70a75ccefc5c | `human_review` | human decision recorded (applied) |
| PASS | destination_recommend/run-70a75ccefc5c | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-4350126a00db | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-4350126a00db | `human_review` | human decision recorded (applied) |
| PASS | destination_recommend/run-4350126a00db | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-bcb3ab0301b7 | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-bcb3ab0301b7 | `human_review` | human decision recorded (applied) |
| PASS | destination_recommend/run-bcb3ab0301b7 | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-b63d4b354473 | `lifecycle` | planner -> worker -> reviewer in order |
| WARN | destination_recommend/run-b63d4b354473 | `human_review` | awaiting human review |
| PASS | destination_recommend/run-b63d4b354473 | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-06fecaa67d88 | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-06fecaa67d88 | `human_review` | human decision recorded (applied) |
| PASS | destination_recommend/run-06fecaa67d88 | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-a4a5990476ea | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-a4a5990476ea | `human_review` | human decision recorded (applied) |
| PASS | destination_recommend/run-a4a5990476ea | `tool_allowlist` | no disallowed tool calls |
| PASS | destination_recommend/run-ae14b831fe4b | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | destination_recommend/run-ae14b831fe4b | `human_review` | human decision recorded (applied) |
| PASS | destination_recommend/run-ae14b831fe4b | `tool_allowlist` | no disallowed tool calls |
| PASS | planner | `latency` | p95 12069 ms (warn above 60000 ms) |
| PASS | reviewer | `latency` | p95 9936 ms (warn above 60000 ms) |
| PASS | worker | `latency` | p95 395 ms (warn above 60000 ms) |
| PASS | planner+reviewer | `fallback_rate` | 1/18 LLM steps used the deterministic fallback |

## Metrics

```json
{
  "runs": 9,
  "by_state": {
    "applied": 7,
    "rejected": 1,
    "awaiting_human_review": 1
  },
  "failure_rate": 0.0,
  "fallback_rate": 0.056,
  "latency_ms": {
    "planner": {
      "p50": 6146,
      "p95": 12069,
      "max": 12069,
      "n": 9
    },
    "reviewer": {
      "p50": 3929,
      "p95": 9936,
      "max": 9936,
      "n": 9
    },
    "worker": {
      "p50": 60,
      "p95": 395,
      "max": 395,
      "n": 9
    }
  }
}
```

## Recommendations

- Review: some runs still await a human decision; review or reject them before release.

## LLM commentary (advisory; never changes the verdict)

**Implementation recommendations:** Findings: P95 latency high (planner 12s, reviewer 9.9s). One run awaiting human review.
Implementation recommendations: Optimize planner and reviewer workflows. Implement auto-retries for awaiting review.

**Review recommendations:** Governance: Planner and reviewer workflows intact.
Risks: P95 latency high (planner 12s, reviewer 9.9s).
Review recommendation: Optimize planner and reviewer workflows.
