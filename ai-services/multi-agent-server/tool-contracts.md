# Multi-Agent Server Contracts

Shared local Multi-Agent Server (not containerised), port **7004**. Containerised backends call
`http://host.docker.internal:7004` (`MULTI_AGENT_SERVICE_URL`). It must be disabled in the cloud
(`MULTI_AGENT_ENABLED=false`).

Envelope (same as the MCP `POST /<tool>` shim):
`{"status": "success", "result": <payload>}` or `{"status": "error", "error": <message or object>}`.

## GET /health
`result`: `status` (`ok`), `service` (`multi-agent-server`), `port`, `ollama_reachable`,
`mcp_reachable`, `rag_reachable` (booleans, 3-second probes), `workflows` (names), and
`workflow_errors` (`{module: error}`) when present.

## GET /workflows
`result`: `[{name, description, allowed_tools, uses_rag}]`, sorted by name.

## POST /workflows/<name>/runs
- Body: `{"input": {...}, "requested_by": "<user or service>"}`
- **201** `result`: `{run_id, workflow, state: "awaiting_human_review", plan, work, review}`
  - `work`: `{items: [{id, ...}], summary, evidence: [...]}`
  - `review`: `{risk: "low"|"medium"|"high", findings: [...], recommendations: [...]}`
- **400** `input` is not an object, or `validate_input` raised `ValueError`. No run is created.
- **404** unknown workflow.
- **500** an agent failed. The run is stored as `failed`.
  `error: {message, run_id, state: "failed", step: "plan"|"work"|"review"}`.

Runs are synchronous. Allow for up to three Ollama calls when you set the client timeout (the CLI uses 600 s).

## GET /runs?workflow=&limit=
`result`: runs, newest first. `limit` defaults to 50 and is capped at 500. A non-integer → 400.

## GET /runs/<run_id>
`result`: `run_id, workflow, state, requested_by, input, plan, work, review, decision,
approved_items, applied, error, created_at, updated_at`. 404 if unknown.

## GET /runs/<run_id>/history
`result`: ordered events `[{seq, ts, step, agent, state, detail}]`.
`step` ∈ `created, plan, work, review, tool_refused, human_review, applied, failed`.
`detail` holds `source`/`attempts`/`duration_ms`/`tools_called` for agent steps, and the decision for `human_review`.

## POST /runs/<run_id>/review
Body:
```json
{"decision": "approve|partial|correct|reject",
 "approved_ids": [], "rejected_ids": [], "edits": {"<id>": {"field": "value"}},
 "note": "...", "reviewer": "..."}
```

| decision | new state | approved item set returned |
|---|---|---|
| `approve` | `approved` | all items (`rejected_ids` not allowed) |
| `partial` | `partially_accepted` | `approved_ids` ∪ edited ids (at least one required) |
| `correct` | `corrected` | all items minus `rejected_ids` (at least one edit required) |
| `reject` | `rejected` | none |

Edits are merged into the returned items. Ids are compared as strings.
- **200** `result`: `{run_id, state, decision, approved_items: [...], rejected_ids: [...]}`.
  The backend applies `approved_items` to its own database.
- **400** unknown decision, unknown ids, an id both approved and rejected, an edit that changes `id`,
  or an edited id that is also rejected. The run stays reviewable.
- **404** unknown run. **409** the run is not `awaiting_human_review` (already decided, failed, or applied).

## POST /runs/<run_id>/applied
Body: `{"created_ids": [...], "target": "<feature>-db"}`. The backend calls this once, after writing.
- **200** `result`: `{run_id, state: "applied", created_ids, target}`
- **400** `created_ids` is not a list, or `target` is missing.
- **409** the run is rejected, already applied, or has no human decision yet.

## Audit log (`logs/audit.jsonl`)
One JSON object per line:
`ts, run_id, workflow, step, agent, input_summary (≤300 chars), output, tools_called[], duration_ms, model, error`.
Agent steps add `source` and `attempts`. `tools_called[]` entries:
`{tool, args|query, transport ("mcp-sdk"|"legacy-shim"), ok, duration_ms, error?, refused?}`.
