"""Optional real headless Edge checks for the static UI, with a controlled HTTP API."""
import copy
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1] / "itinerary-fe"


def test_browser_review_controls_and_safe_rendering(tmp_path):
    websocket = pytest.importorskip("websocket", reason="optional Edge validation needs websocket-client")
    edge = shutil.which("msedge") or next((str(p) for p in (
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ) if p.is_file()), None)
    if not edge:
        pytest.skip("optional UI test: Edge is not installed")
    state = {"enabled": False, "run": None, "requests": [], "number": 0}
    scope = {"trip_reference": "BROWSER-TRIP", "day": 1}
    row = {**scope, "activity_id": 1, "destination_id": 1, "estimated_cost": 10, "notes": '<img src=x onerror="window.injected=true">',
           "start_time": "10:00", "end_time": "11:00"}
    items = [{**row, "itinerary_item_id": i, "id": str(i), "original": {**row, "itinerary_item_id": i},
              "start_time": f"{i+9}:00", "end_time": f"{i+10}:00", "reason": "Remove overlap"} for i in (2, 3)]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.startswith("/api/"):
                result = [] if "multi-agent" not in self.path or self.path.endswith("/history") else state["run"]
                self.reply(200, result if "multi-agent" not in self.path else {"status": "success", "result": result})
                return
            target = ROOT / ("index.html" if self.path == "/" else self.path.lstrip("/"))
            if target not in [ROOT / name for name in ("index.html", "css/styles.css", "js/app.js", "js/multi-agent.js")]:
                self.send_error(404)
                return
            raw = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript" if target.suffix == ".js" else "text/css" if target.suffix == ".css" else "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            state["requests"].append((self.path, body))
            if not state["enabled"]:
                self.reply(403, {"error": "Multi-Agent mode is disabled"})
                return
            if self.path.endswith("/run"):
                state["number"] += 1
                state["run"] = {"run_id": f"run-browser-{state['number']}", "state": "awaiting_human_review", "input": scope,
                                "plan": {"scope": scope}, "work": {"items": copy.deepcopy(items)},
                                "review": {"risk": "medium", "findings": ["Check evidence"]}, "decision": None}
            elif self.path.endswith("/review"):
                state["run"]["decision"] = body
                state["run"]["state"] = {"approve": "approved", "partial": "partially_accepted", "correct": "corrected", "reject": "rejected"}[body["decision"]]
                keep = set(body["approved_ids"]) if body["decision"] == "partial" else set() if body["decision"] == "reject" else {"2", "3"}
                state["run"]["approved_items"] = [{**i, **body["edits"].get(i["id"], {})} for i in items if i["id"] in keep]
            else:
                state["run"]["state"] = "applied"
                state["run"]["applied"] = {"target": "itinerary-db"}
            result = copy.deepcopy(state["run"])
            result["message"] = "Saved approved changes."
            self.reply(201 if self.path.endswith("/run") else 200, {"status": "success", "result": result})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = tmp_path / "edge-profile"
    process = subprocess.Popen(  # noqa: S603 - trusted installed Edge executable, fixed flags, isolated test profile; no shell
        [edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
         "--remote-debugging-port=0", f"--user-data-dir={profile}", f"http://127.0.0.1:{server.server_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    ws = None
    sequence = 0

    def command(method, params):
        nonlocal sequence
        sequence += 1
        ws.send(json.dumps({"id": sequence, "method": method, "params": params}))
        while True:
            result = json.loads(ws.recv())
            if result.get("id") == sequence:
                assert "error" not in result, result
                return result.get("result", {})

    def js(expression):
        result = command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        assert "exceptionDetails" not in result, result
        return result["result"].get("value")

    def wait(expression):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if js(expression):
                return
            time.sleep(.05)
        pytest.fail("Browser condition timed out: " + expression)

    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 20
        while not port_file.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert port_file.exists(), "Edge did not expose its isolated debugging port"
        port = int(port_file.read_text().splitlines()[0])
        targets = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
        target = next(t for t in targets if t["type"] == "page")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        wait("document.readyState === 'complete' && !!document.querySelector('#multi-agent-form')")
        js("document.querySelector('#multi-agent-form [name=trip_reference]').value='BROWSER-TRIP'; document.querySelector('#multi-agent-form [name=day]').value='1'; document.querySelector('#multi-agent-form').requestSubmit()")
        wait("document.querySelector('#multi-agent-status').textContent.includes('disabled')")
        state["enabled"] = True
        for decision in ("approve", "partial", "correct", "reject"):
            js("document.querySelector('#multi-agent-form').requestSubmit()")
            wait("document.querySelector('#multi-agent-status').textContent.includes('awaiting_human_review') && !document.querySelector('#multi-agent-decision button').disabled")
            assert js("['Planner','Worker','Reviewer','Run history'].every(t=>document.querySelector('#multi-agent-output').textContent.includes(t))")
            assert js("!window.injected && !document.querySelector('#multi-agent-output img')")
            assert state["requests"][-1][1] == scope
            if decision == "partial":
                js("document.querySelector('#multi-agent-proposals [name=selected]').checked=false")
            if decision == "correct":
                js("document.querySelector('#multi-agent-proposals [name=notes]').value='Human corrected'")
            js(f"document.querySelector('#multi-agent-decision button[value={decision}]').click()")
            expected = {"approve": "approved", "partial": "partially_accepted", "correct": "corrected", "reject": "rejected"}[decision]
            wait(f"document.querySelector('#multi-agent-status').textContent.includes('{expected}') && !document.querySelector('#multi-agent-refresh').disabled")
            assert js("document.querySelector('#multi-agent-decision button').disabled")
            sent = state["requests"][-1][1]
            assert sent["decision"] == decision
            if decision == "partial":
                assert sent["approved_ids"] == ["3"]
            if decision == "correct":
                assert sent["edits"] == {"2": {"notes": "Human corrected"}}
            if decision == "reject":
                assert js("document.querySelector('#multi-agent-apply').disabled")
            else:
                js("document.querySelector('#multi-agent-apply').click()")
                wait("document.querySelector('#multi-agent-status').textContent.includes('applied') && !document.querySelector('#multi-agent-refresh').disabled")
                assert js("document.querySelector('#multi-agent-apply').disabled")
        command("Browser.close", {})
    finally:
        if ws is not None:
            ws.close()
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
