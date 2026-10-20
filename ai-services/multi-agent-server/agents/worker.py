"""Generic Worker agent: runs workflow.work() with the same timing, logging and
error handling as the Planner and Reviewer. The workflow does the feature work
through RunContext (ctx.mcp / ctx.rag / ctx.llm); this wrapper validates the
result shape and converts any exception into an AgentError that fails the run.
"""
import time

import config
from agents.base import AgentError, AgentResult
from core.workflow import RunContext, validate_work_shape


class WorkerAgent:
    name = "worker"

    def run(self, workflow, payload: dict, plan: dict, ctx: RunContext) -> AgentResult:
        started = time.perf_counter()
        try:
            output = validate_work_shape(workflow.work(payload, plan, ctx))
        except Exception as exc:
            error = AgentError(f"worker failed: {type(exc).__name__}: {exc}")
            error.tools_called = list(ctx.tools_called)
            error.duration_ms = int((time.perf_counter() - started) * 1000)
            raise error from exc
        used_llm = any(call.get("tool") == "llm" for call in ctx.tools_called)
        return AgentResult(
            self.name, output, "workflow", config.ollama_model() if used_llm else None,
            int((time.perf_counter() - started) * 1000), tools_called=list(ctx.tools_called),
        )
