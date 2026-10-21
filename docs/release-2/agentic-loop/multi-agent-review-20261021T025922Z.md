# Multi-Agent Review

- Generated: 20261021T025922Z (UTC) by `ai-services/agentic_loop` mode `multi-agent-review`
- **Verdict: PASS** (pass 16, warn 0, gap 0, fail 0)

## Evidence consulted

- `http://localhost:7004/runs?limit=200`
- `http://localhost:7004/workflows`
- `/tmp/mas-audit.jsonl`

## Checks

| Status | Subject | Check | Detail |
|---|---|---|---|
| PASS | accommodation_shortlist/run-18199a48477d | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | accommodation_shortlist/run-18199a48477d | `human_review` | human decision recorded (rejected) |
| PASS | accommodation_shortlist/run-18199a48477d | `tool_allowlist` | no disallowed tool calls |
| PASS | accommodation_shortlist/run-f3f2d89f1b28 | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | accommodation_shortlist/run-f3f2d89f1b28 | `human_review` | human decision recorded (approved) |
| PASS | accommodation_shortlist/run-f3f2d89f1b28 | `tool_allowlist` | no disallowed tool calls |
| PASS | accommodation_shortlist/run-0417a2081db2 | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | accommodation_shortlist/run-0417a2081db2 | `human_review` | human decision recorded (applied) |
| PASS | accommodation_shortlist/run-0417a2081db2 | `tool_allowlist` | no disallowed tool calls |
| PASS | accommodation_shortlist/run-89d99f843c31 | `lifecycle` | planner -> worker -> reviewer in order |
| PASS | accommodation_shortlist/run-89d99f843c31 | `human_review` | human decision recorded (approved) |
| PASS | accommodation_shortlist/run-89d99f843c31 | `tool_allowlist` | no disallowed tool calls |
| PASS | planner | `latency` | p95 12233 ms (warn above 60000 ms) |
| PASS | reviewer | `latency` | p95 7303 ms (warn above 60000 ms) |
| PASS | worker | `latency` | p95 919 ms (warn above 60000 ms) |
| PASS | planner+reviewer | `fallback_rate` | 0/8 LLM steps used the deterministic fallback |

## Metrics

```json
{
  "runs": 4,
  "by_state": {
    "rejected": 1,
    "approved": 2,
    "applied": 1
  },
  "failure_rate": 0.0,
  "fallback_rate": 0.0,
  "latency_ms": {
    "planner": {
      "p50": 8779,
      "p95": 12233,
      "max": 12233,
      "n": 4
    },
    "reviewer": {
      "p50": 6573,
      "p95": 7303,
      "max": 7303,
      "n": 4
    },
    "worker": {
      "p50": 152,
      "p95": 919,
      "max": 919,
      "n": 4
    }
  }
}
```

## Recommendations

- None: every check passed.

## LLM commentary (advisory; never changes the verdict)

**Implementation recommendations:** Findings: Planner and reviewer latency above warning threshold.
Recommendations: Optimize planner and reviewer workflows; consider parallel processing.

**Review recommendations:** Governance: Latency checks pass, but recommendations not fully supported.
Risks: Planner and reviewer latency warnings not addressed.
Review recommendation: Monitor latency; consider parallel processing if persistent.
