"""Executes a real task against the shared MCP service over Streamable HTTP.

PLAN  - list the live tool registry and select list_destinations.
ACT   - ClientSession.call_tool through the real /mcp transport.
OBSERVE - inspect isError / structuredContent.
ADAPT - deterministic checks decide PASS/FAIL; an empty result triggers one re-plan.
tools.py is never imported.
"""
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from core.reporter import Trace

MCP_SERVICE_URL = os.environ.get("MCP_SERVICE_URL", "http://localhost:7001").rstrip("/")
TIMEOUT_SECONDS = float(os.environ.get("AGENTIC_TIMEOUT_SECONDS", "15"))
EXPECTED_SOURCE = "destination-database"

TASK = {
    "goal": "List seeded destinations in Japan",
    "tool": "list_destinations",
    "arguments": {"country": "Japan"},
}


def _cities(rows):
    return [r.get("city") for r in rows if isinstance(r, dict)]


def evaluate(result, arguments):
    """Return (reason, detail) -- reason is None when the structured result is valid."""
    if getattr(result, "isError", False):
        text = " ".join(getattr(c, "text", "") for c in (result.content or []))
        return "tool_error", text or "isError=true"
    data = result.structuredContent
    if not isinstance(data, dict):
        return "malformed", "structuredContent missing or not an object"
    rows = data.get("destinations")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return "malformed", "destinations missing or not a list of objects"
    if data.get("source") != EXPECTED_SOURCE:
        return "malformed", f"source={data.get('source')!r}, expected {EXPECTED_SOURCE!r}"
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(rows):
        return "malformed", f"count={count!r} does not match {len(rows)} destinations"
    if data.get("filters") != arguments:
        return "malformed", f"filters={data.get('filters')!r} do not echo arguments {arguments!r}"
    return None, ""


async def run_task(session, task, trace):
    """Run the task on an initialised ClientSession. Returns True only when it is satisfied."""
    tool, arguments = task["tool"], task["arguments"]

    # PLAN
    registry = {t.name: t for t in (await session.list_tools()).tools}
    trace.step("PLAN", f"goal={task['goal']!r}; live registry: {sorted(registry)}")
    if tool not in registry:
        trace.step("ADAPT", f"FAIL unregistered: {tool} is not registered; no call made")
        return False
    props = (registry[tool].inputSchema or {}).get("properties", {})
    unsupported = [a for a in arguments if a not in props]
    if unsupported:
        trace.step("ADAPT", f"FAIL unsupported_args: {unsupported} not in {tool} schema {sorted(props)}; no call made")
        return False
    trace.step("PLAN", f"select {tool}; arguments {arguments} are supported by its schema")

    # ACT
    trace.step("ACT", f"call_tool {tool} {arguments} via {MCP_SERVICE_URL}/mcp")
    result = await session.call_tool(tool, arguments)

    # OBSERVE
    data = result.structuredContent if isinstance(result.structuredContent, dict) else {}
    rows = data.get("destinations") if isinstance(data.get("destinations"), list) else []
    trace.step(
        "OBSERVE",
        f"isError={result.isError} source={data.get('source')!r} filters={data.get('filters')} "
        f"count={data.get('count')} cities={_cities(rows)}",
    )

    # ADAPT
    reason, detail = evaluate(result, arguments)
    if reason:
        trace.step("ADAPT", f"FAIL {reason}: {detail}")
        return False
    wrong = [r.get("city") for r in rows if r.get("country") != arguments["country"]]
    if wrong:
        trace.step("ADAPT", f"FAIL unsatisfied: rows outside country={arguments['country']}: {wrong}")
        return False
    if not rows:
        trace.step("ADAPT", "count=0 is not success; re-plan once: call list_destinations with {} to diagnose")
        trace.step("ACT", f"call_tool {tool} {{}} via {MCP_SERVICE_URL}/mcp")
        probe = await session.call_tool(tool, {})
        probe_reason, probe_detail = evaluate(probe, {})
        if probe_reason:
            trace.step("OBSERVE", f"diagnostic isError={probe.isError}")
            trace.step("ADAPT", f"FAIL {probe_reason}: diagnostic call failed: {probe_detail}")
            return False
        total = probe.structuredContent["count"]
        trace.step("OBSERVE", f"unfiltered count={total}")
        if total:
            trace.step("ADAPT", "FAIL unsatisfied: filter matched nothing although the Destination DB has rows")
        else:
            trace.step("ADAPT", "FAIL dependency: Destination DB empty")
        return False
    trace.step(
        "ADAPT",
        f"PASS: task satisfied, {len(rows)} {arguments['country']} destinations from {EXPECTED_SOURCE} "
        f"via registered {tool}",
    )
    return True


async def _run_live(trace):
    async with streamable_http_client(f"{MCP_SERVICE_URL}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await run_task(session, TASK, trace)


async def _run_live_bounded(trace):
    return await asyncio.wait_for(_run_live(trace), timeout=TIMEOUT_SECONDS)


def collect(app_dir=None, repo_root=None) -> tuple:
    trace = Trace("MCP")
    try:
        ok = asyncio.run(_run_live_bounded(trace))
    except BaseException as exc:  # SDK wraps connection errors in ExceptionGroups
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        trace.step(
            "ADAPT",
            f"FAIL transport: shared MCP at {MCP_SERVICE_URL}/mcp unreachable or protocol error "
            f"({type(exc).__name__}); check that the service is running (dependency)",
        )
        ok = False
    return ok, trace.text()
