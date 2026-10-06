"""The single shared VoyageAI FastMCP server and its registered tools.

`python server.py` runs it over stdio for MCP-aware clients (Claude Desktop,
VS Code MCP extension) via mcp-config.json. `python mcp_http_server.py` runs
the same registered tools over Streamable HTTP on port 7001 so Dockerised
feature backends can reach it via http://host.docker.internal:7001/mcp.
"""
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from tools import (
    DestinationToolError,
    ci_report,
    create_accommodation,
    get_accommodation_by_destination,
    get_itinerary,
    list_destinations,
    list_expenses,
    project_files,
    search_accommodations,
)

# host.docker.internal is allowed so containerised backends pass the
# DNS-rebinding Host/Origin check that FastMCP applies to /mcp.
ALLOWED_HOSTS = ["localhost:*", "127.0.0.1:*", "host.docker.internal:*"]

mcp = FastMCP(
    "VoyageAI Shared MCP",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", "7001")),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=[f"http://{host}" for host in ALLOWED_HOSTS],
    ),
)


@mcp.tool(name="get_itinerary", annotations=ToolAnnotations(readOnlyHint=True))
def get_itinerary_tool(trip_reference: str, day: int | None = None) -> dict[str, Any]:
    """Read itinerary records for exactly one trip, optionally restricted to one day."""
    return get_itinerary(trip_reference, day)


@mcp.tool(name="list_expenses")
def list_expenses_tool(trip_reference: str = None) -> dict[str, Any]:
    return list_expenses(trip_reference)


@mcp.tool(name="get_accommodation_by_destination")
def get_accommodation_by_destination_tool(destination: str) -> dict[str, Any]:
    return get_accommodation_by_destination(destination)


@mcp.tool(name="search_accommodations")
def search_accommodations_tool(
    destination: str | None = None,
    accommodation_type: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    amenities: str | None = None,
    sort_by: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    return search_accommodations(
        destination, accommodation_type, min_price, max_price, min_rating, amenities, sort_by, limit
    )


@mcp.tool(name="create_accommodation")
def create_accommodation_tool(
    name: str | None = None,
    destination_id: str | None = None,
    price_per_night: float | None = None,
    destination_city: str | None = None,
    accommodation_type: str | None = None,
    rating: float | None = None,
    location: str | None = None,
    description: str | None = None,
    amenities: list[str] | str | None = None,
) -> dict[str, Any]:
    return create_accommodation(
        name, destination_id, price_per_night, destination_city,
        accommodation_type, rating, location, description, amenities,
    )


@mcp.tool(name="project_files")
def project_files_tool(directory_path: str = ".") -> dict[str, Any]:
    return project_files(directory_path)


@mcp.tool(name="ci_report")
def ci_report_tool(feature: str = "budget-service") -> dict[str, Any]:
    return ci_report(feature)


@mcp.tool(name="list_destinations", annotations=ToolAnnotations(readOnlyHint=True))
def list_destinations_tool(
    city: str | None = None, country: str | None = None, travel_style: str | None = None
) -> dict[str, Any]:
    """Read destinations from the Destination Database API (exact-match, case-sensitive filters).

    Returns { source, filters, count, destinations[] }; invalid input or a
    destination-db failure is reported as an MCP tool error (isError=true).
    """
    try:
        return list_destinations(city=city, country=country, travel_style=travel_style)
    except DestinationToolError as exc:
        raise ToolError(str(exc)) from exc


def registered_tool_names() -> list[str]:
    return [tool.name for tool in mcp._tool_manager.list_tools()]


if __name__ == "__main__":
    print("Starting VoyageAI Shared MCP Server (stdio)...")
    print("Available tools:")
    for tool in registered_tool_names():
        print(f"- {tool}")
    mcp.run()
