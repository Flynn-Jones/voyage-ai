"""Drives the Activities MCP and RAG tabs in real (headless) Chrome and saves screenshots.

Needs the stack from docs/reports/rag-mcp-local-validation.md running, plus
`pip install playwright` (it uses the system Chrome, so no browser download).
Run from activity-service/:
    python tests/ui_demo.py docs/reports/screenshots
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:3003"
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


def card_text(page, log_id):
    card = page.locator(f"#{log_id} > div").first
    card.wait_for(timeout=120_000)
    return card.inner_text()


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})

    # MCP: Run a tool
    page.goto(f"{BASE}/mcp-mode")
    page.select_option("#mcp-tool", "get_activity_assignments")
    page.fill("#mcp-record-id", "9")
    page.click("#mcp-tool-form button")
    print("=== MCP Mode: get_activity_assignments(9) ===")
    print(card_text(page, "mcp-log"))
    page.screenshot(path=str(OUT / "mcp-mode-tool-call.png"), full_page=True)

    # RAG: toggle ON, Answer with citations
    page.goto(f"{BASE}/rag-mode")
    if not page.is_checked("#rag-toggle"):
        page.check("#rag-toggle")
    page.fill("#rag-answer-form input[name=query]", "When is the Live Jazz Club Evening scheduled?")
    page.click("#rag-answer-form button")
    print("\n=== RAG Mode (ON): answer ===")
    print(card_text(page, "rag-log"))
    page.screenshot(path=str(OUT / "rag-mode-answer.png"), full_page=True)

    # RAG: toggle OFF persists across reload and blocks calls
    page.uncheck("#rag-toggle")
    page.reload()
    print("\n=== RAG toggle after reload:", "ON" if page.is_checked("#rag-toggle") else "OFF", "===")
    page.click("#rag-refresh-form button")
    print(card_text(page, "rag-log"))
    page.screenshot(path=str(OUT / "rag-mode-off-blocked.png"), full_page=True)
    page.check("#rag-toggle")  # leave the demo in the ON state

    browser.close()
