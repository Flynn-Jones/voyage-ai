"""Client for the shared MCP server, with a per-workflow tool allowlist.

Primary transport is the official MCP Python SDK over Streamable HTTP
(MCP_SERVICE_URL, default http://localhost:7001/mcp). If the SDK transport
cannot connect, the call falls back to the legacy POST /<tool_name> shim on the
same host. A tool error reported by the server (isError / {"error": ...}) is a
real answer and is returned as {"error": ...}, not retried on the shim.
"""
import asyncio
import json

import requests

import config


class ToolNotAllowedError(PermissionError):
    """The workflow tried to call an MCP tool that is not on its allowlist."""


class MCPUnavailableError(RuntimeError):
    """Neither the SDK transport nor the legacy shim could be reached."""


def legacy_base_url() -> str:
    url = config.mcp_service_url()
    return url[: -len("/mcp")] if url.endswith("/mcp") else url


def _normalise(structured, text_blocks):
    if isinstance(structured, dict):
        # FastMCP wraps non-object returns as {"result": value}.
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    for text in text_blocks:
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            continue
    return {"text": "\n".join(text_blocks)}


async def _sdk_call(url: str, tool: str, arguments: dict):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    texts = [c.text for c in result.content if hasattr(c, "text")]
    if result.isError:
        return {"error": "; ".join(texts) or "tool reported isError"}
    return _normalise(result.structuredContent, texts)


def _sdk_call_sync(tool: str, arguments: dict):
    return asyncio.run(
        asyncio.wait_for(_sdk_call(config.mcp_service_url(), tool, arguments), config.service_timeout())
    )


def _legacy_call(tool: str, arguments: dict):
    try:
        response = requests.post(
            f"{legacy_base_url()}/{tool}", json=arguments, timeout=config.service_timeout()
        )
    except requests.RequestException as exc:
        raise MCPUnavailableError(f"mcp legacy shim unreachable: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        return {"error": f"mcp legacy shim returned non-JSON ({response.status_code})"}
    if body.get("status") == "success":
        return body.get("result")
    result = body.get("result")
    if isinstance(result, dict) and "error" in result:
        return result
    return {"error": body.get("error") or f"mcp legacy shim returned {response.status_code}"}


class MCPClient:
    """Calls MCP tools on behalf of one workflow run, enforcing its allowlist."""

    def __init__(self, allowed_tools, sdk_call=None, legacy_call=None):
        self.allowed_tools = list(allowed_tools)
        self._sdk_call = sdk_call or _sdk_call_sync
        self._legacy_call = legacy_call or _legacy_call

    def call(self, tool: str, arguments: dict | None = None) -> tuple[object, str]:
        """Return (result, transport). Raises ToolNotAllowedError before any network I/O."""
        if tool not in self.allowed_tools:
            raise ToolNotAllowedError(
                f"tool '{tool}' is not in this workflow's allowlist {self.allowed_tools}"
            )
        arguments = dict(arguments or {})
        try:
            return self._sdk_call(tool, arguments), "mcp-sdk"
        except ToolNotAllowedError:
            raise
        except Exception as sdk_exc:  # transport failure: try the legacy shim
            try:
                return self._legacy_call(tool, arguments), "legacy-shim"
            except MCPUnavailableError as legacy_exc:
                raise MCPUnavailableError(f"sdk: {sdk_exc}; {legacy_exc}") from legacy_exc


def reachable() -> bool:
    try:
        return requests.get(f"{legacy_base_url()}/health", timeout=config.PROBE_TIMEOUT_SECONDS).ok
    except requests.RequestException:
        return False
