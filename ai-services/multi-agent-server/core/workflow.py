"""The workflow plugin contract every feature codes against.

A workflow is pure feature logic: prompts, a deterministic fallback for each
LLM step, and the Worker body. The generic agents in agents/ own timing,
logging, JSON validation, retry and fallback; RunContext owns tool access.
See README.md ("Workflow plugin contract") for the documented interface.
"""
import re
import time

from clients import rag_client
from clients.mcp_client import MCPUnavailableError, ToolNotAllowedError

NAME_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")  # snake_case with a feature prefix
RISK_LEVELS = ("low", "medium", "high")


class Workflow:
    name: str = ""
    description: str = ""
    allowed_tools: list[str] = []
    uses_rag: bool = False

    # ---- required: implemented by every workflow --------------------------
    def validate_input(self, payload: dict) -> dict:
        raise NotImplementedError

    def planner_prompt(self, payload: dict) -> str:
        raise NotImplementedError

    def fallback_plan(self, payload: dict) -> dict:
        raise NotImplementedError

    def work(self, payload: dict, plan: dict, ctx: "RunContext") -> dict:
        raise NotImplementedError

    def reviewer_prompt(self, payload: dict, plan: dict, work: dict) -> str:
        raise NotImplementedError

    def fallback_review(self, payload: dict, plan: dict, work: dict) -> dict:
        raise NotImplementedError

    # ---- optional: override to tighten validation -------------------------
    def validate_plan(self, plan, payload: dict) -> dict:
        """Default: a JSON object; any step naming a tool must name an allowlisted one.

        Applied to the LLM plan and to fallback_plan(); raise ValueError to reject.
        """
        if not isinstance(plan, dict) or not plan:
            raise ValueError("plan must be a non-empty JSON object")
        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("plan.steps must be a list")
        for step in steps:
            tool = step.get("tool") if isinstance(step, dict) else None
            if tool is not None and tool not in self.allowed_tools:
                raise ValueError(f"plan uses tool '{tool}' outside allowed_tools")
        return plan

    def validate_review(self, review) -> dict:
        return validate_review_shape(review)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "uses_rag": bool(self.uses_rag),
        }


def validate_review_shape(review) -> dict:
    if not isinstance(review, dict):
        raise ValueError("review must be a JSON object")
    if review.get("risk") not in RISK_LEVELS:
        raise ValueError(f"review.risk must be one of {RISK_LEVELS}")
    for key in ("findings", "recommendations"):
        if not isinstance(review.get(key), list):
            raise ValueError(f"review.{key} must be a list")
    return review


def validate_work_shape(work) -> dict:
    if not isinstance(work, dict):
        raise ValueError("work must return a dict")
    items = work.get("items")
    if not isinstance(items, list):
        raise ValueError("work.items must be a list")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError("every work item must be an object with an 'id'")
        key = str(item["id"])
        if key in seen:
            raise ValueError(f"duplicate work item id '{key}'")
        seen.add(key)
    if not isinstance(work.get("summary"), str):
        raise ValueError("work.summary must be a string")
    if not isinstance(work.get("evidence"), list):
        raise ValueError("work.evidence must be a list")
    return work


class RunContext:
    """Tool access for one Worker run. Every call is timed and recorded in tools_called."""

    def __init__(self, run_id, workflow: Workflow, mcp_client, llm_fn, on_refused=None, rag_fn=None):
        self.run_id = run_id
        self.workflow = workflow
        self._mcp = mcp_client
        self._llm = llm_fn
        self._rag = rag_fn or rag_client.call
        self._on_refused = on_refused or (lambda entry: None)
        self.tools_called: list[dict] = []

    def _record(self, entry: dict, started: float) -> dict:
        entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
        self.tools_called.append(entry)
        return entry

    def mcp(self, tool: str, args: dict | None = None):
        """Call an allowlisted MCP tool. Returns the tool result, or {"error": ...} on failure.

        Raises ToolNotAllowedError (after logging the refusal) for a tool not on the allowlist.
        """
        started = time.perf_counter()
        try:
            result, transport = self._mcp.call(tool, args or {})
        except ToolNotAllowedError as exc:
            entry = self._record({"tool": tool, "args": args or {}, "refused": True, "ok": False,
                                  "error": str(exc)}, started)
            self._on_refused(entry)
            raise
        except MCPUnavailableError as exc:
            self._record({"tool": tool, "args": args or {}, "ok": False, "error": str(exc)}, started)
            return {"error": str(exc)}
        failed = isinstance(result, dict) and "error" in result
        self._record({"tool": tool, "args": args or {}, "transport": transport, "ok": not failed,
                      **({"error": result["error"]} if failed else {})}, started)
        return result

    def rag(self, query: str, k: int = 5, endpoint: str = "retrieve", **scope) -> dict:
        """Call the shared RAG server (/retrieve or /answer). Requires uses_rag = True."""
        tool = f"rag:{endpoint}"
        started = time.perf_counter()
        if not self.workflow.uses_rag:
            exc = ToolNotAllowedError(f"workflow '{self.workflow.name}' has uses_rag = False")
            entry = self._record({"tool": tool, "refused": True, "ok": False, "error": str(exc)}, started)
            self._on_refused(entry)
            raise exc
        try:
            result = self._rag(endpoint, query, k, **scope)
        except rag_client.RAGUnavailableError as exc:
            self._record({"tool": tool, "query": query, "ok": False, "error": str(exc)}, started)
            return {"status": "error", "error": str(exc)}
        ok = result.get("status") in ("success", "insufficient_context")
        self._record({"tool": tool, "query": query, "ok": ok,
                      **({} if ok else {"error": result.get("error") or result.get("error_type")})}, started)
        return result

    def llm(self, prompt: str, system: str | None = None, json_mode: bool = True):
        """Ask Ollama. Returns a parsed dict (json_mode) or text, or None if the call failed."""
        started = time.perf_counter()
        try:
            result = self._llm(prompt, system=system, json_mode=json_mode)
            self._record({"tool": "llm", "ok": result is not None}, started)
            return result
        except Exception as exc:
            self._record({"tool": "llm", "ok": False, "error": str(exc)}, started)
            return None
