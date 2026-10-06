"""Official-SDK client probe for the shared MCP service (Streamable HTTP).

  python mcp_probe.py list
  python mcp_probe.py call list_destinations '{"country": "Japan"}'

Exits non-zero on connection failure or isError=true.
"""
import argparse
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run(url, command, tool, arguments):
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if command == "list":
                tools = await session.list_tools()
                print(json.dumps([t.name for t in tools.tools], indent=2))
                return 0
            result = await session.call_tool(tool, arguments)
            print(json.dumps(
                {
                    "isError": result.isError,
                    "structuredContent": result.structuredContent,
                    "text": [c.text for c in result.content if hasattr(c, "text")],
                },
                indent=2,
            ))
            return 1 if result.isError else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:7001/mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    call = sub.add_parser("call")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()

    try:
        arguments = json.loads(args.arguments) if args.command == "call" else None
        sys.exit(asyncio.run(run(args.url, args.command, getattr(args, "tool", None), arguments)))
    except Exception as exc:
        print(f"probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
