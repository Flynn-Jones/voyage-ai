"""Shared machinery for the generic Planner / Worker / Reviewer agents.

An LLM step is: ask Ollama for strict JSON → parse → validate → on failure
retry once (telling the model what was wrong) → on a second failure use the
workflow's deterministic fallback. Every agent returns an AgentResult so the
runner can log all three the same way.
"""
import json
import time
from dataclasses import dataclass, field

import config
from clients import ollama_client

MAX_ATTEMPTS = 2  # first try + one retry


class AgentError(RuntimeError):
    """An agent could not produce a valid output (fallback included). Fails the run."""


@dataclass
class AgentResult:
    agent: str
    output: dict
    source: str  # "llm" | "fallback" | "workflow"
    model: str | None
    duration_ms: int
    attempts: list = field(default_factory=list)
    tools_called: list = field(default_factory=list)


def parse_json_object(text: str) -> dict:
    """Parse a model reply as a single JSON object, tolerating ```json fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("reply is valid JSON but not an object")
    return value


def llm_json(prompt: str, system: str | None = None, json_mode: bool = True, chat=None):
    """One raw LLM call for RunContext.llm. Returns a dict (json_mode) or text."""
    chat = chat or ollama_client.chat
    reply = chat(system or "Respond with a single JSON object only.", prompt, json_mode=json_mode)
    return parse_json_object(reply) if json_mode else reply


class LLMJsonAgent:
    """Planner and Reviewer: LLM JSON with validate → retry once → fallback."""

    name = "agent"
    system_prompt = "Respond with a single JSON object only."

    def __init__(self, chat=None):
        self._chat = chat or ollama_client.chat

    def _run(self, prompt: str, validate, fallback) -> AgentResult:
        started = time.perf_counter()
        attempts = []
        user_prompt = prompt
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                reply = self._chat(self.system_prompt, user_prompt, json_mode=True)
                output = validate(parse_json_object(reply))
                attempts.append({"attempt": attempt, "ok": True})
                return AgentResult(self.name, output, "llm", config.ollama_model(),
                                   _elapsed(started), attempts)
            except Exception as exc:
                attempts.append({"attempt": attempt, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                user_prompt = (
                    f"{prompt}\n\nYour previous reply was rejected: {exc}. "
                    "Reply again with ONLY a valid JSON object in the required shape."
                )
        try:
            output = validate(fallback())
        except Exception as exc:
            raise AgentError(f"{self.name} fallback failed: {exc}") from exc
        return AgentResult(self.name, output, "fallback", None, _elapsed(started), attempts)


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
