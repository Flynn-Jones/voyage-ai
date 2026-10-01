"""Stdio MCP server exposing the shared VoyageAI tools over the Model Context Protocol.

Launched by MCP-aware clients (Claude Desktop, VS Code MCP extension) via
mcp-config.json. Containerised backends instead reach the same tool logic
over HTTP via mcp_http_server.py, since a stdio server can't be dialled from
inside a Docker container.
"""
from mcp.server.fastmcp import FastMCP

from tools import (
    ci_report,
    create_accommodation,
    get_accommodation_by_destination,
    list_expenses,
    project_files,
    search_accommodations,
)

mcp = FastMCP("VoyageAI Shared MCP")
AVAILABLE_TOOLS = [
    "list_expenses",
    "get_accommodation_by_destination",
    "search_accommodations",
    "create_accommodation",
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
def search_accommodations_tool(
    destination: str = None,
    accommodation_type: str = None,
    min_price: float = None,
    max_price: float = None,
    min_rating: float = None,
    amenities: str = None,
    sort_by: str = None,
    limit: int = 20,
):
    return search_accommodations(
        destination, accommodation_type, min_price, max_price, min_rating, amenities, sort_by, limit
    )


@mcp.tool()
def create_accommodation_tool(
    name: str = None,
    destination_id: str = None,
    price_per_night: float = None,
    destination_city: str = None,
    accommodation_type: str = None,
    rating: float = None,
    location: str = None,
    description: str = None,
    amenities: list = None,
):
    return create_accommodation(name, destination_id, price_per_night, destination_city,
                                accommodation_type, rating, location, description, amenities)


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
