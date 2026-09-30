"""Stdio MCP server exposing the shared VoyageAI tools over the Model Context Protocol.

Launched by MCP-aware clients (Claude Desktop, VS Code MCP extension) via
mcp-config.json. Containerised backends instead reach the same tool logic
over HTTP via mcp_http_server.py, since a stdio server can't be dialled from
inside a Docker container.
"""
from mcp.server.fastmcp import FastMCP

from tools import (
    ci_report,
    get_accommodation_by_destination,
    list_expenses,
    project_files,
)

mcp = FastMCP("VoyageAI Shared MCP")
AVAILABLE_TOOLS = [
    "list_expenses",
    "get_accommodation_by_destination",
    "project_files",
    "ci_report",
]


@mcp.tool()
def list_expenses_tool(trip_reference: str = None):
    return list_expenses(trip_reference)


@mcp.tool()
def get_accommodation_by_destination_tool(destination: str):
    return get_accommodation_by_destination(destination)


@mcp.tool()
def project_files_tool(directory_path: str = "."):
    return project_files(directory_path)


@mcp.tool()
def ci_report_tool(feature: str = "budget-service"):
    return ci_report(feature)


if __name__ == "__main__":
    print("Starting VoyageAI Shared MCP Server...")
    print("Server status: RUNNING")
    print("Available tools:")
    for tool in AVAILABLE_TOOLS:
        print(f"- {tool}")
    mcp.run()
