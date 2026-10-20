"""CLI smoke test against a real in-process HTTP server (Ollama/MCP/RAG faked)."""
import json

import cli


def run_cli(capsys, url, *argv):
    code = cli.main(["--url", url, *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_end_to_end(live_server, capsys):
    code, out, _ = run_cli(capsys, live_server, "health")
    assert code == 0 and "multi-agent-server" in out

    code, out, _ = run_cli(capsys, live_server, "workflows")
    assert code == 0 and "example_readonly" in out and "list_destinations" in out

    code, out, _ = run_cli(capsys, live_server, "--json", "run", "example_readonly", "--input", "{}")
    assert code == 0
    run_id = json.loads(out)["result"]["run_id"]

    code, out, _ = run_cli(capsys, live_server, "show", run_id)
    assert code == 0 and "awaiting_human_review" in out and "REVIEW  risk=medium" in out

    code, out, _ = run_cli(capsys, live_server, "review", run_id, "--decision", "partial",
                           "--approve", "1", "--edit", '2:{"count": 7}', "--note", "trim")
    assert code == 0 and "partially_accepted" in out and '"count": 7' in out

    code, out, err = run_cli(capsys, live_server, "review", run_id, "--decision", "approve")
    assert code == 1 and "409" in err

    code, out, _ = run_cli(capsys, live_server, "applied", run_id, "--created", "5,6", "--target", "test-db")
    assert code == 0

    code, out, _ = run_cli(capsys, live_server, "history", run_id)
    assert code == 0
    assert [line.split()[2] for line in out.strip().splitlines()] == [
        "created", "plan", "work", "review", "human_review", "human_review", "applied"]

    code, out, _ = run_cli(capsys, live_server, "runs", "--workflow", "example_readonly")
    assert code == 0 and run_id in out and "applied" in out


def test_cli_rejects_bad_edit_and_unreachable_server(capsys):
    try:
        cli.main(["review", "run-x", "--decision", "correct", "--edit", "no-colon"])
    except SystemExit as exc:
        assert exc.code == 2
    code, _, err = run_cli(capsys, "http://127.0.0.1:9", "workflows")
    assert code == 2 and "cannot reach" in err


def test_cli_survives_non_utf8_console(live_server, monkeypatch):
    # Found live: `cli.py workflows | ...` crashed on example_readonly's arrow under cp1252 (Windows).
    import io
    import sys

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    assert cli.main(["--url", live_server, "workflows"]) == 0
    console.flush()
    assert b"Planner ? Worker" in raw.getvalue()
