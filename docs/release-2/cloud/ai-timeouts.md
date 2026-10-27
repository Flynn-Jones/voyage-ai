# AI request timeouts

Ollama runs **CPU-only** on the shared EC2 AI host (`voyageai-ai-host`, `m7i-flex.large`,
`qwen2.5:1.5b`). A single generation takes tens of seconds and a Multi-Agent run takes
minutes. The original timeouts were tuned for a fast local box, so slow-but-healthy
inference surfaced in the UI as a transport error — a failure message for work the backend
had actually completed.

This file is the one place the whole ladder is written down. The code comments point here.

## The rule

**Every hop must outwait the hop it calls.** If an outer layer gives up first, the user
sees `Read timed out` instead of the inner service's real error, and the work is wasted
because nothing propagates the cancellation.

```
browser
  └─ frontend / nginx          480s      ← must exceed the backend ceiling
       └─ backend → Multi-Agent 420s     ← 3 sequential Ollama calls + retries
            └─ MAS → MCP/RAG    240s
                 └─ Ollama       180s    ← one generation
       └─ backend → RAG          240s    ← retrieval + one grounded generation
       └─ backend → MCP           60s    ← plain database reads, no model
       └─ backend → Ollama       180s    ← direct AI Mode / chat / review
```

## Values

| Hop | Timeout | Env var |
| --- | --- | --- |
| Ollama, one generation | 180s | `OLLAMA_TIMEOUT_SECONDS` |
| backend → MCP | 60s | `MCP_TIMEOUT_SECONDS` |
| backend → RAG | 240s | `RAG_TIMEOUT_SECONDS` |
| Multi-Agent Server → MCP/RAG | 240s | `MULTI_AGENT_SERVICE_TIMEOUT_SECONDS` |
| backend → Multi-Agent Server | 420s | `MULTI_AGENT_TIMEOUT_SECONDS` |
| frontend → backend, AI Mode | 240s | `AI_REQUEST_TIMEOUT_SECONDS` |
| frontend → backend, MCP | 60–90s | `MCP_REQUEST_TIMEOUT_SECONDS` |
| frontend → backend, RAG | 300s | `RAG_REQUEST_TIMEOUT_SECONDS` |
| frontend → backend, Multi-Agent | 480s | `MULTI_AGENT_REQUEST_TIMEOUT_SECONDS` |

Every value is an environment-variable default, so the fleet can be retuned through
`scripts/deploy/aws/services.yml` (a task-definition change only — **no image rebuild**)
without touching code. `budget-fe` and `itinerary-fe` are nginx, so theirs is
`proxy_read_timeout` in their `nginx.conf` and *does* need a rebuild.

## Measured latencies

Taken live against the deployed stack, and what the headroom is for:

| Path | Measured | Ceiling | Was |
| --- | --- | --- | --- |
| destination `/compare` | ~21s | 180s | 120s |
| accommodation `/recommend` | ~91s | 180s | 180s |
| activity `/ai-mode/chat` | ~7s | 180s | **30s** |
| budget `/api/budget/ai-analyse` | ~48s | 180s | **60s** |
| itinerary `/api/itinerary/ai-review` | ~56s | 180s | **60s** |
| activity Multi-Agent run (3 agents) | 139s | 420s | 240s |

The bolded ones were inside their own margin: budget at 48s against a 60s ceiling and
itinerary at 56s against nginx's 60s default were one slow generation away from failing,
and did.

## Things that bit us

- **nginx's default `proxy_read_timeout` is 60s.** `itinerary-fe` set it for
  `/api/itinerary/multi-agent/` but not for the general `/api/` location, so `ai-review`
  silently inherited 60s. A missing directive is not a visible number — grep for the
  directive, not for a value you dislike.
- **A parameter default is not configurable.** accommodation's `generate_recommendation`
  took `timeout: int = 180`, which no deployment could override. It now falls back to
  `OLLAMA_TIMEOUT_SECONDS`.
- **The frontend can hide a working backend.** activity used one `(5, 60)` for every call,
  so a 139s Multi-Agent run that completed server-side was reported to the user as a
  failure. Timeouts are a chain; raising one layer just moves the failure.
- **Raising a timeout does not make anything faster.** These numbers stop false failures.
  If a path is genuinely too slow, the fix is a smaller model, a shorter prompt, or a GPU
  host — not a bigger number.

## Changing them

Keep the ordering. `ai-services/multi-agent-server/tests/test_timeout_ladder.py` asserts
it, and `activity-service/tests/test_frontend_multi_agent.py` pins activity's own budget,
so an inversion fails CI rather than reaching a user.
