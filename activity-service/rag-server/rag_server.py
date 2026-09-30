"""Stdio MCP server exposing the Activity Manager RAG tools over the Model Context Protocol.

Launched by MCP-aware clients via mcp-config.json. The containerised backend
reaches the same pipeline over HTTP via rag_http_server.py instead, since a
stdio server can't be dialled from inside a Docker container.
"""
import sys

from mcp.server.fastmcp import FastMCP

import rag_pipeline

mcp = FastMCP("Activity Manager RAG MCP")
AVAILABLE_TOOLS = ["refresh_corpus", "retrieve_context", "answer_question"]


# FastMCP builds each tool's schema from the signature and docstring, so the
# docstrings below are what an LLM client actually sees when choosing a tool.
@mcp.tool(name="refresh_corpus")
def refresh_corpus_tool(caller: str = "mcp_client") -> dict:
    """Rebuild the activity knowledge corpus (activities DB, docs, repo index) and re-index it in ChromaDB."""
    return rag_pipeline.refresh_corpus(caller=caller)


@mcp.tool(name="retrieve_context")
def retrieve_context_tool(query: str, k: int = 5, caller: str = "mcp_client") -> dict:
    """Return the top-k corpus chunks for a short query, each with chunk_id, source_id, authority tier and distance. Read-only."""
    return rag_pipeline.retrieve_context(query, k, caller=caller)


@mcp.tool(name="answer_question")
def answer_question_tool(query: str, k: int = 5, caller: str = "mcp_client") -> dict:
    """Answer a question about activities using only retrieved context, with citations and a confidence category."""
    return rag_pipeline.answer_question(query, k, caller=caller)


if __name__ == "__main__":
    # Banner as in mcp-server/server.py, but on stderr: stdout is the stdio
    # transport, and any non-protocol bytes there corrupt the client's stream.
    print("Starting Activity Manager RAG MCP Server...", file=sys.stderr)
    print("Server status: RUNNING", file=sys.stderr)
    print("Available tools:", file=sys.stderr)
    for tool in AVAILABLE_TOOLS:
        print(f"- {tool}", file=sys.stderr)
    mcp.run()
