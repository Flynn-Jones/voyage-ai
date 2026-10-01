import sys
from pathlib import Path

AGENTIC_DIR = Path(__file__).resolve().parent.parent
MCP_SERVER_DIR = AGENTIC_DIR.parent / "mcp-server"
for path in (AGENTIC_DIR, MCP_SERVER_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
