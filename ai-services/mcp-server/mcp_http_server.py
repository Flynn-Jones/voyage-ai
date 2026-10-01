"""Shared MCP service over Streamable HTTP (port 7001, not containerised).

The real MCP endpoint is POST /mcp, served by the FastMCP instance in
server.py. Dockerised feature backends reach it via
http://host.docker.internal:7001/mcp, so every call goes through FastMCP's
tool registry.

POST /<tool_name> is a compatibility shim for the Budget backend's existing
client: it dispatches through mcp.call_tool (registry + argument validation),
never by importing tool functions directly.
"""
import json

from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from server import mcp, registered_tool_names


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "mcp-server",
            "transport": "streamable-http",
            "mcp_path": mcp.settings.streamable_http_path,
            "tools": registered_tool_names(),
        }
    )


@mcp.custom_route("/{tool_name}", methods=["POST"])
async def legacy_tool_call(request: Request) -> JSONResponse:
    tool_name = request.path_params["tool_name"]
    if tool_name not in registered_tool_names():
        return JSONResponse({"status": "error", "error": f"unknown tool: {tool_name}"}, status_code=404)

    try:
        raw = await request.body()
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as exc:
        return JSONResponse({"status": "error", "error": f"invalid_json: {exc}"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"status": "error", "error": "invalid_json: body must be an object"}, status_code=400)

    try:
        outcome = await mcp.call_tool(tool_name, payload)
    except ToolError as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=502)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)

    # call_tool returns (unstructured_content, structured_result) for structured tools.
    result = outcome[1] if isinstance(outcome, tuple) else outcome
    failed = isinstance(result, dict) and "error" in result
    return JSONResponse(
        {"status": "error" if failed else "success", "result": result},
        status_code=502 if failed else 200,
    )


def create_app():
    return mcp.streamable_http_app()


def main():
    print(f"Shared MCP (streamable-http) on {mcp.settings.host}:{mcp.settings.port}{mcp.settings.streamable_http_path}")
    print(f"Registered tools: {', '.join(registered_tool_names())}")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
