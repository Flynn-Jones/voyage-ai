"""Run state (SQLite) and the append-only audit log (JSONL).

runs.db holds one row per run plus an ordered events table (the run history),
so runs survive a server restart. logs/audit.jsonl gets one line per agent
step / human decision / apply, with the fields listed in AUDIT_FIELDS.
"""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

AUDIT_FIELDS = ("ts", "run_id", "workflow", "step", "agent", "input_summary", "output",
                "tools_called", "duration_ms", "model", "error")
JSON_COLUMNS = ("input", "plan", "work", "review", "decision", "approved_items", "applied")
SUMMARY_LIMIT = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    state TEXT NOT NULL,
    requested_by TEXT,
    input TEXT, plan TEXT, work TEXT, review TEXT,
    decision TEXT, approved_items TEXT, applied TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    ts TEXT NOT NULL,
    step TEXT NOT NULL,
    agent TEXT,
    state TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_runs_workflow ON runs(workflow, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def summarise(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= SUMMARY_LIMIT else text[: SUMMARY_LIMIT - 3] + "..."


class Store:
    def __init__(self, db_path: Path | None = None, audit_path: Path | None = None):
        self.db_path = Path(db_path or config.db_path())
        self.audit_path = Path(audit_path or config.audit_log_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        """Commit on success, roll back on error, and always close (sqlite3's own `with` does not close)."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---- runs --------------------------------------------------------------
    def create_run(self, workflow: str, payload: dict, requested_by: str) -> str:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        ts = now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, workflow, state, requested_by, input, created_at, updated_at)"
                " VALUES (?, ?, 'running', ?, ?, ?, ?)",
                (run_id, workflow, requested_by, json.dumps(payload), ts, ts),
            )
        return run_id

    def transition(self, run_id: str, expected: tuple, new_state: str, **fields) -> bool:
        """Atomically move a run from one of `expected` states to `new_state`. False if it was not in one."""
        fields["state"] = new_state
        fields["updated_at"] = now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = [json.dumps(v, default=str) if k in JSON_COLUMNS else v for k, v in fields.items()]
        placeholders = ", ".join("?" for _ in expected)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ? AND state IN ({placeholders})",  # noqa: S608 - column names are code constants
                [*values, run_id, *expected],
            )
            return cursor.rowcount == 1

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, workflow: str | None = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM runs"
        params: list = []
        if workflow:
            query += " WHERE workflow = ?"
            params.append(workflow)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_run(row) for row in rows]

    def fail_interrupted_runs(self) -> list[str]:
        """Runs left 'running' by a crash/restart can never finish: mark them failed."""
        with self._connect() as conn:
            ids = [r["run_id"] for r in conn.execute("SELECT run_id FROM runs WHERE state = 'running'")]
        for run_id in ids:
            error = "server restarted while the run was in progress"
            if self.transition(run_id, ("running",), "failed", error=error):
                self.add_event(run_id, "failed", "server", "failed", {"error": error})
        return ids

    # ---- history -----------------------------------------------------------
    def add_event(self, run_id: str, step: str, agent: str | None, state: str | None, detail=None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (run_id, ts, step, agent, state, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, now(), step, agent, state, json.dumps(detail or {}, default=str)),
            )

    def history(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT seq, ts, step, agent, state, detail FROM events WHERE run_id = ? ORDER BY seq",
                (run_id,),
            ).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"] or "{}")} for row in rows]

    # ---- audit -------------------------------------------------------------
    def audit(self, *, run_id, workflow, step, agent, input_summary=None, output=None,
              tools_called=None, duration_ms=None, model=None, error=None, **extra) -> dict:
        entry = {
            "ts": now(), "run_id": run_id, "workflow": workflow, "step": step, "agent": agent,
            "input_summary": summarise(input_summary) if input_summary is not None else None,
            "output": output, "tools_called": tools_called or [], "duration_ms": duration_ms,
            "model": model, "error": error, **extra,
        }
        line = json.dumps(entry, default=str)
        with self._lock, open(self.audit_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return entry

    def read_audit(self, run_id: str | None = None) -> list[dict]:
        if not self.audit_path.exists():
            return []
        entries = []
        with open(self.audit_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if run_id is None or entry.get("run_id") == run_id:
                    entries.append(entry)
        return entries


def _row_to_run(row) -> dict:
    run = dict(row)
    for key in JSON_COLUMNS:
        run[key] = json.loads(run[key]) if run.get(key) else None
    return run
