"""Shared fixtures. Unit tests import budget-be directly (Multi-Agent Server and budget-db mocked);
endpoint tests (test_endpoints.py) run against live containers and are marked `live`."""
import sys
from pathlib import Path

BUDGET_BE = Path(__file__).resolve().parent.parent / "budget-be"
if str(BUDGET_BE) not in sys.path:
    sys.path.insert(0, str(BUDGET_BE))
