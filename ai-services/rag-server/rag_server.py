"""Stdio MCP server exposing the shared RAG pipeline over the Model Context Protocol."""
from mcp.server.fastmcp import FastMCP

from rag_pipeline import answer_question as answer_question_impl
from rag_pipeline import refresh_corpus as refresh_corpus_impl
from rag_pipeline import retrieve_context as retrieve_context_impl

mcp = FastMCP("VoyageAI Shared RAG")
AVAILABLE_TOOLS = ["refresh_corpus", "retrieve_context", "answer_question"]


@mcp.tool()
def refresh_corpus(caller: str = "system"):
    return refresh_corpus_impl(caller=caller)


@mcp.tool()
def retrieve_context(query: str, k: int = 5, caller: str = "system"):
    return retrieve_context_impl(query=query, k=k, caller=caller)


@mcp.tool()
def answer_question(query: str, k: int = 5, caller: str = "system"):
    return answer_question_impl(query=query, k=k, caller=caller)


if __name__ == "__main__":
    print("Starting VoyageAI Shared RAG Server...")
    print("Server status: RUNNING")
    print("Available tools:")
    for tool in AVAILABLE_TOOLS:
        print(f"- {tool}")
    mcp.run()
