"""Run lifecycle: Planner → Worker → Reviewer → Human Review → applied.

    running → awaiting_human_review → approved | partially_accepted | corrected | rejected → applied
    (any agent failure while running → failed)

Every step writes one history event (runs.db) and one audit entry (audit.jsonl).
"""
import copy
from functools import partial

from agents.base import llm_json
from agents.planner import PlannerAgent
from agents.reviewer import ReviewerAgent
from agents.worker import WorkerAgent
from clients import ollama_client
from clients.mcp_client import MCPClient
from core.workflow import RunContext

DECISION_STATES = {
    "approve": "approved",
    "partial": "partially_accepted",
    "correct": "corrected",
    "reject": "rejected",
}
APPLICABLE_STATES = ("approved", "partially_accepted", "corrected")


class RunError(Exception):
    http_status = 500

    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


class BadRequest(RunError):
    http_status = 400


class NotFound(RunError):
    http_status = 404


class Conflict(RunError):
    http_status = 409


class RunFailed(RunError):
    http_status = 500


class Runner:
    def __init__(self, store, registry, chat=None, mcp_factory=None, rag_fn=None):
        self.store = store
        self.registry = registry
        self.chat = chat or ollama_client.chat
        self.mcp_factory = mcp_factory or MCPClient
        self.rag_fn = rag_fn
        self.planner = PlannerAgent(chat=self.chat)
        self.worker = WorkerAgent()
        self.reviewer = ReviewerAgent(chat=self.chat)

    # ---- start a run -------------------------------------------------------
    def start_run(self, workflow_name: str, body: dict) -> dict:
        workflow = self.registry.get(workflow_name)
        if workflow is None:
            raise NotFound(f"unknown workflow: {workflow_name}")
        raw_input = body.get("input", {})
        if not isinstance(raw_input, dict):
            raise BadRequest("input must be a JSON object")
        try:
            payload = workflow.validate_input(raw_input)
        except ValueError as exc:
            raise BadRequest(f"invalid input: {exc}") from exc
        requested_by = str(body.get("requested_by") or "unknown")

        run_id = self.store.create_run(workflow.name, payload, requested_by)
        self.store.add_event(run_id, "created", None, "running", {"requested_by": requested_by})
        log = partial(self.store.audit, run_id=run_id, workflow=workflow.name)

        step = "plan"
        try:
            plan_result = self.planner.run(workflow, payload)
            self._record_step(run_id, log, "plan", plan_result, payload)

            step = "work"
            ctx = RunContext(
                run_id, workflow, self.mcp_factory(workflow.allowed_tools),
                llm_fn=partial(llm_json, chat=self.chat),
                on_refused=lambda entry: self._record_refusal(run_id, log, entry),
                rag_fn=self.rag_fn,
            )
            work_result = self.worker.run(workflow, payload, plan_result.output, ctx)
            self._record_step(run_id, log, "work", work_result, plan_result.output)

            step = "review"
            review_result = self.reviewer.run(workflow, payload, plan_result.output, work_result.output)
            self._record_step(run_id, log, "review", review_result, work_result.output.get("summary"))
        except Exception as exc:
            self._fail(run_id, log, step, exc)
            raise RunFailed(f"run {run_id} failed during {step}: {exc}",
                            payload={"run_id": run_id, "state": "failed", "step": step}) from exc

        self.store.transition(
            run_id, ("running",), "awaiting_human_review",
            plan=plan_result.output, work=work_result.output, review=review_result.output,
        )
        self.store.add_event(run_id, "human_review", "human", "awaiting_human_review")
        return {
            "run_id": run_id,
            "workflow": workflow.name,
            "state": "awaiting_human_review",
            "plan": plan_result.output,
            "work": work_result.output,
            "review": review_result.output,
        }

    def _record_step(self, run_id, log, step, result, step_input) -> None:
        self.store.add_event(run_id, step, result.agent, "running", {
            "source": result.source, "attempts": result.attempts, "duration_ms": result.duration_ms,
            "tools_called": [c.get("tool") for c in result.tools_called],
        })
        log(step=step, agent=result.agent, input_summary=step_input, output=result.output,
            tools_called=result.tools_called, duration_ms=result.duration_ms, model=result.model,
            source=result.source, attempts=result.attempts)

    def _record_refusal(self, run_id, log, entry) -> None:
        self.store.add_event(run_id, "tool_refused", "worker", "running",
                             {"tool": entry.get("tool"), "error": entry.get("error")})
        log(step="tool_refused", agent="worker", input_summary={"tool": entry.get("tool")},
            tools_called=[entry], duration_ms=entry.get("duration_ms"), error=entry.get("error"))

    def _fail(self, run_id, log, step, exc) -> None:
        error = f"{step}: {exc}"
        self.store.transition(run_id, ("running",), "failed", error=error)
        self.store.add_event(run_id, "failed", step_agent(step), "failed", {"step": step, "error": str(exc)})
        log(step=step, agent=step_agent(step), tools_called=getattr(exc, "tools_called", []),
            duration_ms=getattr(exc, "duration_ms", None), error=str(exc))

    # ---- human review ------------------------------------------------------
    def review(self, run_id: str, body: dict) -> dict:
        run = self._get(run_id)
        decision = body.get("decision")
        if decision not in DECISION_STATES:
            raise BadRequest(f"decision must be one of {sorted(DECISION_STATES)}")
        if run["state"] != "awaiting_human_review":
            raise Conflict(f"run {run_id} is '{run['state']}', not awaiting_human_review; "
                           "only one human decision is allowed per run")
        try:
            approved_items, rejected_ids = resolve_decision(run["work"]["items"], decision, body)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

        new_state = DECISION_STATES[decision]
        record = {
            "decision": decision,
            "approved_ids": [str(item["id"]) for item in approved_items],
            "rejected_ids": rejected_ids,
            "edits": body.get("edits") or {},
            "note": body.get("note"),
            "reviewer": body.get("reviewer") or "unknown",
        }
        if not self.store.transition(run_id, ("awaiting_human_review",), new_state,
                                     decision=record, approved_items=approved_items):
            raise Conflict(f"run {run_id} was already reviewed")
        self.store.add_event(run_id, "human_review", "human", new_state, record)
        self.store.audit(run_id=run_id, workflow=run["workflow"], step="human_review", agent="human",
                         input_summary={"decision": decision, "reviewer": record["reviewer"]},
                         output=record, duration_ms=0)
        return {"run_id": run_id, "state": new_state, "decision": decision,
                "approved_items": approved_items, "rejected_ids": rejected_ids}

    # ---- backend confirms it applied the approved items ---------------------
    def applied(self, run_id: str, body: dict) -> dict:
        run = self._get(run_id)
        created_ids = body.get("created_ids")
        target = body.get("target")
        if not isinstance(created_ids, list):
            raise BadRequest("created_ids must be a list")
        if not isinstance(target, str) or not target.strip():
            raise BadRequest("target is required, e.g. \"budget-db\"")
        if run["state"] == "rejected":
            raise Conflict(f"run {run_id} was rejected and cannot be applied")
        if run["state"] == "applied":
            raise Conflict(f"run {run_id} has already been applied")
        if run["state"] not in APPLICABLE_STATES:
            raise Conflict(f"run {run_id} is '{run['state']}'; it needs a human decision first")

        applied = {"created_ids": created_ids, "target": target.strip()}
        if not self.store.transition(run_id, APPLICABLE_STATES, "applied", applied=applied):
            raise Conflict(f"run {run_id} has already been applied")
        self.store.add_event(run_id, "applied", "backend", "applied", applied)
        self.store.audit(run_id=run_id, workflow=run["workflow"], step="applied", agent="backend",
                         input_summary=applied, output=applied, duration_ms=0)
        return {"run_id": run_id, "state": "applied", **applied}

    def _get(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            raise NotFound(f"unknown run: {run_id}")
        return run


def step_agent(step: str) -> str:
    return {"plan": "planner", "work": "worker", "review": "reviewer"}.get(step, step)


def _id_list(body: dict, key: str) -> list[str]:
    value = body.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return [str(v) for v in value]


def resolve_decision(items: list[dict], decision: str, body: dict) -> tuple[list[dict], list[str]]:
    """Return (final approved items with edits applied, rejected ids) for a human decision."""
    order = [str(item["id"]) for item in items]
    by_id = {str(item["id"]): item for item in items}
    approved_ids = _id_list(body, "approved_ids")
    rejected_ids = _id_list(body, "rejected_ids")
    edits = body.get("edits") or {}
    if not isinstance(edits, dict):
        raise ValueError("edits must be an object of {id: {field: value}}")
    edits = {str(k): v for k, v in edits.items()}

    unknown = [i for i in [*approved_ids, *rejected_ids, *edits] if i not in by_id]
    if unknown:
        raise ValueError(f"unknown item ids: {sorted(set(unknown))}")
    if set(approved_ids) & set(rejected_ids):
        raise ValueError("an item cannot be both approved and rejected")
    for item_id, changes in edits.items():
        if not isinstance(changes, dict) or not changes:
            raise ValueError(f"edits['{item_id}'] must be a non-empty object")
        if "id" in changes:
            raise ValueError("edits cannot change an item's id")
        if item_id in rejected_ids:
            raise ValueError(f"item {item_id} is both edited and rejected")

    if decision == "approve":
        if rejected_ids:
            raise ValueError("approve accepts every item; use 'partial' to reject some")
        keep = set(order)
    elif decision == "partial":
        keep = set(approved_ids) | set(edits)
        if not keep:
            raise ValueError("partial needs approved_ids (or edits)")
    elif decision == "correct":
        if not edits:
            raise ValueError("correct needs at least one edit")
        keep = set(order) - set(rejected_ids)
    else:  # reject
        keep = set()

    approved = []
    for item_id in order:
        if item_id in keep:
            item = copy.deepcopy(by_id[item_id])
            item.update(edits.get(item_id, {}))
            approved.append(item)
    return approved, [i for i in order if i not in keep]
