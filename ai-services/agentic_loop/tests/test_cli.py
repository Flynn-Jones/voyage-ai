import builtins

import pytest

import app_main
from core import orchestrator


@pytest.fixture
def stubs(monkeypatch):
    state = {"results": {"mcp": (True, "mcp-trace"), "rag": (True, "rag-trace"), "activity_rag": (True, "act")},
             "collected": [], "llm_calls": [], "llm_error": None}

    def make(key):
        def collect(app_dir, repo_root):
            state["collected"].append(key)
            return state["results"][key]
        return collect

    monkeypatch.setattr(orchestrator, "COLLECTORS", {k: make(k) for k in state["results"]})

    def fake_call(system, user, review=False):
        state["llm_calls"].append(review)
        return (None, state["llm_error"]) if state["llm_error"] else ("ok comment", None)

    monkeypatch.setattr(orchestrator.ai, "call", fake_call)
    return state


def test_mcp_pass_exit_0_no_llm(stubs, capsys):
    assert app_main.main(["--mode", "mcp", "--no-llm"]) == 0
    assert stubs["llm_calls"] == []
    assert "VERDICT PASS" in capsys.readouterr().out


def test_failed_collector_exit_1_and_no_llm(stubs, capsys):
    stubs["results"]["mcp"] = (False, "trace [ADAPT] FAIL transport")
    assert app_main.main(["--mode", "mcp"]) == 1
    assert stubs["llm_calls"] == []
    assert "VERDICT FAIL" in capsys.readouterr().out


def test_llm_error_does_not_flip_pass(stubs, capsys):
    stubs["llm_error"] = "Ollama unavailable"
    assert app_main.main(["--mode", "rag"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT PASS" in out and "LLM COMMENTARY UNAVAILABLE" in out


def test_llm_commentary_runs_after_pass(stubs):
    assert app_main.main(["--mode", "mcp"]) == 0
    assert stubs["llm_calls"] == [False, True]


@pytest.mark.parametrize("mcp_ok,rag_ok,code", [(True, True, 0), (False, True, 1), (True, False, 1), (False, False, 1)])
def test_all_combines_verdicts(stubs, mcp_ok, rag_ok, code):
    stubs["results"]["mcp"] = (mcp_ok, "m")
    stubs["results"]["rag"] = (rag_ok, "r")
    assert app_main.main(["--mode", "all", "--no-llm"]) == code
    assert stubs["collected"] == ["mcp", "rag"]  # Activity stays opt-in


def test_activity_rag_is_valid_choice(stubs):
    assert app_main.main(["--mode", "activity_rag", "--no-llm"]) == 0
    assert stubs["collected"] == ["activity_rag"]


def test_invalid_mode_rejected():
    with pytest.raises(SystemExit):
        app_main.main(["--mode", "bogus"])


def test_no_args_keeps_interactive_menu(stubs, monkeypatch, capsys):
    answers = iter(["1", "0"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    assert app_main.main([]) == 0
    out = capsys.readouterr().out
    assert "VALIDATION MENU" in out and "VERDICT PASS" in out and "Exiting." in out


# --- optional LLM isolation (Codex blocker) ---
class _Resp:
    def __init__(self, body=None, text=None, status_code=200):
        self._body, self.text, self.status_code = body, text if text is not None else str(body), status_code

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


MALFORMED = [
    _Resp(ValueError("Expecting value"), text="<html>oops</html>"),
    _Resp([1, 2]),
    _Resp({"nope": 1}),
    _Resp({"message": {}}),
    _Resp({"message": {"content": None}}),
    _Resp({"message": {"content": 42}}),
    _Resp({"message": {"content": ["x"]}}),
]


@pytest.mark.parametrize("resp", MALFORMED)
def test_ai_call_malformed_200_is_controlled(monkeypatch, resp):
    from core import ai
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: resp)
    output, err = ai.call("s", "u")
    assert output is None and err and "Unexpected Ollama response" in err
    assert len(err) < 400


def test_ai_call_valid_response(monkeypatch):
    from core import ai
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: _Resp({"message": {"content": " hi "}}))
    assert ai.call("s", "u") == ("hi", None)


@pytest.mark.parametrize("resp", MALFORMED)
def test_malformed_ollama_after_pass_keeps_pass_exit_0(stubs, monkeypatch, capsys, resp):
    monkeypatch.undo()  # restore real ai.call; re-apply collector stubs below
    monkeypatch.setattr(orchestrator, "COLLECTORS", {"mcp": lambda a, r: (True, "trace")})
    monkeypatch.setattr(orchestrator.ai.requests, "post", lambda *a, **k: resp)
    assert app_main.main(["--mode", "mcp"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT PASS" in out and "LLM COMMENTARY UNAVAILABLE" in out


def test_ai_call_raising_after_pass_is_isolated(stubs, monkeypatch, capsys):
    def boom(*a, **k):
        raise ValueError("boom")
    monkeypatch.setattr(orchestrator.ai, "call", boom)
    assert app_main.main(["--mode", "mcp"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT PASS" in out and "LLM COMMENTARY UNAVAILABLE" in out and "ValueError" in out
    assert "OVERALL VERDICT PASS" in out


def test_review_call_raising_is_isolated(stubs, monkeypatch):
    calls = []

    def flaky(system, user, review=False):
        calls.append(review)
        if review:
            raise RuntimeError("review boom")
        return "ok", None

    monkeypatch.setattr(orchestrator.ai, "call", flaky)
    assert app_main.main(["--mode", "rag"]) == 0
    assert calls == [False, True]


def test_failed_collector_never_invokes_llm_even_if_it_would_raise(stubs, monkeypatch):
    stubs["results"]["rag"] = (False, "[RAG][ADAPT] FAIL")
    monkeypatch.setattr(orchestrator.ai, "call", lambda *a, **k: pytest.fail("LLM invoked"))
    assert app_main.main(["--mode", "rag"]) == 1
