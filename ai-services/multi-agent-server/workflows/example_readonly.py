"""Neutral example workflow, for testing the lifecycle only (no feature logic).

Planner picks read-only MCP tools from the allowlist, the Worker calls them and
counts the records each returned, and the Reviewer flags empty or failed calls.
Copy this file as the starting point for a feature workflow.
"""
import json

from core.workflow import RunContext, Workflow

# Where each tool's records live in its result (see mcp-server/tool-contracts.md).
RECORD_KEYS = {"list_destinations": "destinations", "list_expenses": "expenses"}


class ExampleReadonlyWorkflow(Workflow):
    name = "example_readonly"
    description = "Counts records from read-only MCP tools; proves Planner → Worker → Reviewer → Human Review."
    allowed_tools = ["list_destinations", "list_expenses"]
    uses_rag = False

    def validate_input(self, payload: dict) -> dict:
        trip_reference = payload.get("trip_reference")
        if trip_reference is not None and (not isinstance(trip_reference, str) or len(trip_reference) > 100):
            raise ValueError("trip_reference must be a string of at most 100 characters")
        tools = payload.get("tools", self.allowed_tools)
        if not isinstance(tools, list) or not tools or any(t not in self.allowed_tools for t in tools):
            raise ValueError(f"tools must be a non-empty subset of {self.allowed_tools}")
        return {"trip_reference": trip_reference, "tools": tools}

    def planner_prompt(self, payload: dict) -> str:
        return (
            "Plan which read-only tools to call to count the records available.\n"
            f"Tools the user asked for: {payload['tools']}\n"
            f"trip_reference (pass to list_expenses only, if not null): {json.dumps(payload['trip_reference'])}\n"
            'Return JSON: {"steps": [{"tool": "<tool>", "args": {}}], "rationale": "<one sentence>"}'
        )

    def fallback_plan(self, payload: dict) -> dict:
        steps = []
        for tool in payload["tools"]:
            args = {"trip_reference": payload["trip_reference"]} if tool == "list_expenses" and payload["trip_reference"] else {}
            steps.append({"tool": tool, "args": args})
        return {"steps": steps, "rationale": "deterministic fallback: call each requested tool once"}

    def validate_plan(self, plan, payload: dict) -> dict:
        plan = super().validate_plan(plan, payload)
        if not plan.get("steps"):
            raise ValueError("plan must contain at least one step")
        for step in plan["steps"]:
            if not isinstance(step, dict) or "tool" not in step:
                raise ValueError("every step needs a tool")
            if step["tool"] not in payload["tools"]:
                raise ValueError(f"plan uses '{step['tool']}' but the user only asked for {payload['tools']}")
            if not isinstance(step.get("args", {}), dict):
                raise ValueError("step.args must be an object")
        return plan

    def work(self, payload: dict, plan: dict, ctx: RunContext) -> dict:
        items = []
        for index, step in enumerate(plan["steps"], start=1):
            result = ctx.mcp(step["tool"], step.get("args") or {})
            error = result.get("error") if isinstance(result, dict) else None
            records = result.get(RECORD_KEYS.get(step["tool"], ""), []) if isinstance(result, dict) else []
            count = result.get("count", len(records)) if isinstance(result, dict) and not error else 0
            items.append({"id": index, "tool": step["tool"], "count": count, "ok": error is None, "error": error})
        total = sum(item["count"] for item in items)  # arithmetic stays in Python, never in the LLM
        return {
            "items": items,
            "summary": f"{len(items)} tool call(s), {total} record(s) in total",
            "evidence": [{"tool": i["tool"], "count": i["count"], "error": i["error"]} for i in items],
        }

    def reviewer_prompt(self, payload: dict, plan: dict, work: dict) -> str:
        return (
            "Review these tool results. Flag any tool that returned zero records or an error.\n"
            f"Results: {json.dumps(work['items'])}\n"
            'Return JSON: {"risk": "low|medium|high", "findings": ["..."], "recommendations": ["..."]}'
        )

    def fallback_review(self, payload: dict, plan: dict, work: dict) -> dict:
        failed = [i for i in work["items"] if not i["ok"]]
        empty = [i for i in work["items"] if i["ok"] and i["count"] == 0]
        findings = [f"{i['tool']} failed: {i['error']}" for i in failed]
        findings += [f"{i['tool']} returned no records" for i in empty]
        recommendations = []
        if failed:
            recommendations.append("Check the MCP server and feature database tiers are running.")
        if empty:
            recommendations.append("Seed the empty feature database or confirm the filter is correct.")
        risk = "high" if failed else "medium" if empty else "low"
        return {"risk": risk, "findings": findings, "recommendations": recommendations}


WORKFLOW = ExampleReadonlyWorkflow
