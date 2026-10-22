"""activity_schedule_check - Activity Manager (Student 3) workflow for the shared Multi-Agent Server.

One job, like the other features' workflows: check one day's schedule. A single run lists the
activities scheduled that day, finds every overlapping pair, and proposes free slots that clash
with nothing.

Boundaries:
- Read-only: list_activities and list_assignments only. Never writes to the activity database.
- A free slot is a "Proposed:" item for a human to accept; nothing is scheduled or saved.
- The activity schema has no trip, location or date column. A "day" is the date part of an
  assignment's assignment_time, and evidence is limited to the fields the database really has.
Agent prompts are activity-service/prompts/multi-agent/*.txt. Times, overlaps and free slots are
computed in Python; the LLM only plans, narrates the computed result and phrases the review, and
both are checked against the computed facts before a human sees them.
"""
import json
import re
import threading
from datetime import date, datetime

import config
from core.workflow import RunContext, Workflow

PROMPTS_DIR = config.REPO_ROOT / "activity-service" / "prompts" / "multi-agent"

# tool -> (key holding its records in the MCP result, what it is evidence for)
SOURCES = {
    "list_activities": ("activities", "activity names, types, costs and durations"),
    "list_assignments": ("assignments", "scheduled start times (assignment_time)"),
}
AGENTS = ("Planner", "Worker", "Reviewer", "Human")
DECISIONS = ("Accept", "Partially Accept", "Reject")  # least to most strict
RANK = {"low": 0, "medium": 1, "high": 2}

DAY_START, DAY_END = 8 * 60, 20 * 60  # free slots are searched 08:00-20:00 (minutes after midnight)
MAX_REQUEST = 300
MAX_SLOTS = 3
MAX_EVIDENCE_LINES = 40  # keeps the Worker prompt short enough for a 0.5b model
WORKER_WORDS = 100
WORKER_HEADINGS = ("Output:", "Evidence Used:", "Limitations:")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WRITE_WORDS = re.compile(r"\b(create|insert|update|delete|remove|write|save|book)\b", re.IGNORECASE)
# "#7", "activity_id 7", "assignment_id: 7", "id=7": ids the Worker/Reviewer claims to cite.
ID_MENTION = re.compile(r"(?:\b(?:activity_id|assignment_id|id)\s*[:=]?\s*|#)(\d+)\b", re.IGNORECASE)

# check code -> risk level. Codes in PARTIAL make the rule-based recommendation Partially Accept.
CHECK_LEVELS = {
    "no_data": "high", "proposal_conflict": "high",
    "unsupported_claim": "medium", "format": "medium", "unknown_duration": "medium",
    "bad_time": "medium", "orphan_assignment": "medium", "clash": "medium",
    "too_long": "low", "no_records": "low", "no_free_slot": "low",
}
PARTIAL = {"unsupported_claim", "format", "unknown_duration", "bad_time", "orphan_assignment", "too_long"}

# reviewer_prompt() -> validate_review() hand-off for the current request thread (see validate_review).
_REVIEW_CONTEXT = threading.local()


def _prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def duration_minutes(text) -> int | None:
    """Parse the free-text duration ("2 hours", "45 min", "1h 30m", "half-day") into minutes.

    None when it cannot be parsed confidently; callers flag it instead of guessing.
    """
    t = str(text or "").strip().lower()
    if not t:
        return None
    if "half" in t and "day" in t:
        return 240
    if ("full" in t or "whole" in t) and "day" in t:
        return 480
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", t)
    mins = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes)\b", t)
    if hours or mins:
        total = (float(hours.group(1)) * 60 if hours else 0) + (int(mins.group(1)) if mins else 0)
        return round(total) or None
    if re.fullmatch(r"\d+(?:\.\d+)?", t):  # a bare number means hours, as in the backend's parser
        return round(float(t) * 60) or None
    return None


def _parse_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _money(value) -> float | None:
    return round(float(value), 2) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class ActivityScheduleCheckWorkflow(Workflow):
    name = "activity_schedule_check"
    description = ("Activity: check one day's schedule - the activities on it, any that overlap, and "
                   "clash-free slots - for human review (read-only; never schedules anything).")
    allowed_tools = list(SOURCES)
    uses_rag = False

    # ---- input --------------------------------------------------------------
    def validate_input(self, payload: dict) -> dict:
        day = payload.get("date") or None
        if day is None:
            raise ValueError("date (YYYY-MM-DD) is required")
        if not isinstance(day, str) or not DATE_RE.match(day.strip()):
            raise ValueError("date must be YYYY-MM-DD")
        try:
            day = date.fromisoformat(day.strip()).isoformat()
        except ValueError as exc:
            raise ValueError("date must be a real calendar date (YYYY-MM-DD)") from exc
        slot_hours = payload.get("slot_hours", 2)
        if (isinstance(slot_hours, bool) or not isinstance(slot_hours, (int, float))
                or not 0.25 <= slot_hours <= 12):
            raise ValueError("slot_hours must be a number from 0.25 to 12")
        request = payload.get("request") or ""
        if not isinstance(request, str) or len(request) > MAX_REQUEST:
            raise ValueError(f"request must be a string of at most {MAX_REQUEST} characters")
        return {"date": day, "slot_hours": float(slot_hours), "request": request.strip()}

    def _task_text(self, payload: dict) -> str:
        return (f"Check the schedule on {payload['date']}: list the activities scheduled that day, find any "
                f"that overlap, and propose free {payload['slot_hours']:g}-hour slots between "
                f"{_hhmm(DAY_START)} and {_hhmm(DAY_END)} that clash with none of them")

    # ---- plan ---------------------------------------------------------------
    def planner_prompt(self, payload: dict) -> str:
        sources = "\n".join(f"- {tool}: {why}" for tool, (_, why) in SOURCES.items())
        request = f"User request: {json.dumps(payload['request'])}\n" if payload["request"] else ""
        return (
            f"{_prompt('planner_prompt.txt')}\n\n"
            f"Task: {self._task_text(payload)}.\n{request}"
            f"Evidence sources (read-only MCP tools, both required):\n{sources}"
        )

    def fallback_plan(self, payload: dict) -> dict:
        return {
            "objective": self._task_text(payload) + ".",
            "steps": [
                {"agent": "Worker", "action": "Read every activity", "tool": "list_activities"},
                {"agent": "Worker", "action": "Read every scheduled time", "tool": "list_assignments"},
                {"agent": "Reviewer", "action": "Check the output against the evidence"},
                {"agent": "Human", "action": "Accept, Partially Accept or Reject"},
            ],
            "evidence_required": [f"MCP:{tool}" for tool in SOURCES],
            "human_approval": "A human decides; no change is written to the activity database.",
        }

    def validate_plan(self, plan, payload: dict) -> dict:
        if not isinstance(plan, dict):
            raise ValueError("plan must be a JSON object")
        objective = plan.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("plan needs an objective")
        steps = plan.get("steps")
        if not isinstance(steps, list) or not 3 <= len(steps) <= 5:
            raise ValueError("plan needs 3 to 5 steps")
        clean, read = [], set()
        for step in steps:
            agent = step.get("agent") if isinstance(step, dict) else None
            action = step.get("action") if isinstance(step, dict) else None
            if agent not in AGENTS:
                raise ValueError(f"each step's agent must be one of {AGENTS}, got {agent!r}")
            if not isinstance(action, str) or not action.strip():
                raise ValueError(f"{agent} step needs an action")
            if WRITE_WORDS.search(action) and not action.strip().lower().startswith("propose"):
                raise ValueError(f"plans never write to the database: {action!r}")
            entry = {"agent": agent, "action": action.strip()}
            tool = step.get("tool")
            if agent == "Worker" and tool is not None:
                if tool not in SOURCES:
                    raise ValueError(f"plan uses '{tool}', which is not an allowed read-only source")
                entry["tool"] = tool
                read.add(tool)
            clean.append(entry)
        missing = sorted(set(SOURCES) - read)
        if missing:
            raise ValueError(f"plan must have Worker steps reading {missing}")
        if not any(step["agent"] == "Human" for step in clean):
            raise ValueError("plan must end with a Human approval step")
        evidence = plan.get("evidence_required")
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
            raise ValueError("evidence_required must be a list of strings")
        approval = plan.get("human_approval")
        if not isinstance(approval, str) or not approval.strip():
            raise ValueError("plan needs human_approval")
        # Tool arguments are always built by work() from validated input, never taken from the LLM.
        return {"objective": objective.strip(), "steps": clean, "evidence_required": evidence,
                "human_approval": approval.strip()}

    # ---- work ---------------------------------------------------------------
    def _read(self, ctx: RunContext, tool: str, evidence: list) -> list | None:
        key = SOURCES[tool][0]
        result = ctx.mcp(tool, {})
        rows = result.get(key) if isinstance(result, dict) else None
        if not isinstance(rows, list) or "error" in result:
            detail = result.get("error") if isinstance(result, dict) else "unexpected result"
            evidence.append({"source": tool, "status": "no_data", "detail": f"no data from {tool}: {detail}"})
            return None
        evidence.append({"source": tool, "status": "ok", "count": len(rows),
                         "detail": f"{len(rows)} record(s) from {tool}"})
        return rows

    def work(self, payload: dict, plan: dict, ctx: RunContext) -> dict:
        day, slot_hours = payload["date"], payload["slot_hours"]
        evidence: list[dict] = []
        activities = self._read(ctx, "list_activities", evidence)
        assignments = self._read(ctx, "list_assignments", evidence)
        by_id = {a.get("activity_id"): a for a in activities or [] if isinstance(a, dict)}

        problems = []  # schedule facts the reviewer must see: unparseable times/durations, orphans
        schedule = []
        for row in assignments or []:
            if not isinstance(row, dict):
                continue
            activity = by_id.get(row.get("activity_id"))
            start = _parse_time(row.get("assignment_time"))
            entry = {"assignment_id": row.get("assignment_id"), "activity_id": row.get("activity_id"),
                     "name": (activity or {}).get("activity_name"), "time": row.get("assignment_time"),
                     "start": start, "minutes": duration_minutes((activity or {}).get("duration"))}
            if activity is None and activities is not None:
                problems.append({"code": "orphan_assignment",
                                 "text": f"Assignment #{entry['assignment_id']} points at activity "
                                         f"#{entry['activity_id']}, which is not in list_activities."})
            if start is None:
                problems.append({"code": "bad_time",
                                 "text": f"Assignment #{entry['assignment_id']} has an unreadable time "
                                         f"{entry['time']!r}; it was left out."})
            schedule.append(entry)
        on_day = sorted((s for s in schedule if s["start"] and s["start"].date().isoformat() == day),
                        key=lambda s: s["start"])
        for s in on_day:
            if s["minutes"] is None and s["name"] is not None:
                problems.append({"code": "unknown_duration",
                                 "text": f"Activity #{s['activity_id']} '{s['name']}' at {s['time']} has a "
                                         f"duration that cannot be read "
                                         f"({by_id[s['activity_id']].get('duration')!r})."})

        # The two kinds of item the human accepts or rejects: clashing pairs, then proposed slots.
        overlaps = self._overlaps(on_day)
        # Without the schedule a "free" slot is a guess, so none is proposed.
        have_schedule = activities is not None and assignments is not None
        slots = self._free_slots(on_day, day, slot_hours) if have_schedule else []
        scheduled = [{"assignment_id": s["assignment_id"], "activity_id": s["activity_id"], "name": s["name"],
                      "time": s["time"], "duration": (by_id.get(s["activity_id"]) or {}).get("duration")}
                     for s in on_day]

        shown_ids = {s["activity_id"] for s in on_day}
        lines = [f"- #{a.get('activity_id')} | {a.get('activity_name')} | {a.get('activity_type')} | "
                 f"cost {a.get('activity_cost')} | duration {a.get('duration')} [MCP:list_activities]"
                 for a in activities or [] if a.get("activity_id") in shown_ids]
        lines += [f"- assignment #{s['assignment_id']} | activity #{s['activity_id']} | {s['time']} "
                  f"[MCP:list_assignments]" for s in on_day]
        truncated = len(lines) > MAX_EVIDENCE_LINES

        work = {
            "items": overlaps + slots,
            "scheduled": scheduled,
            "summary": self._summary(on_day, overlaps, slots, day, slot_hours),
            "evidence": evidence,
            "activity_evidence": lines[:MAX_EVIDENCE_LINES],
            "totals": {"date": day, "slot_hours": slot_hours, "activities": len(activities or []),
                       "assignments": len(schedule), "scheduled_on_day": len(on_day),
                       "overlaps": len(overlaps), "free_slots": len(slots),
                       "items": len(overlaps) + len(slots), "evidence_truncated": truncated},
            "problems": problems,
            # every id the evidence contains; the Worker/Reviewer may only cite these
            "known_ids": sorted({str(i) for i in by_id if i is not None}
                                | {str(s["assignment_id"]) for s in schedule if s["assignment_id"] is not None}),
            "busy": [[s["start"].strftime("%H:%M"), s["minutes"]] for s in on_day],
        }
        work["worker_output"], work["worker_output_source"] = self._narrate(payload, plan, work, ctx)
        work["checks"] = self.checks(work)
        return work

    @staticmethod
    def _summary(on_day: list, overlaps: list, slots: list, day: str, slot_hours: float) -> str:
        if not on_day:
            head = f"No activities scheduled on {day}"
        else:
            head = f"{len(on_day)} activit{'y' if len(on_day) == 1 else 'ies'} scheduled on {day}"
        clash = f"{len(overlaps)} overlapping pair(s)" if overlaps else "no overlaps"
        free = (f"{len(slots)} proposed {slot_hours:g}h slot(s) that clash with nothing" if slots
                else f"no free {slot_hours:g}h slot between {_hhmm(DAY_START)} and {_hhmm(DAY_END)}")
        return f"{head}; {clash}; {free}."

    def _overlaps(self, on_day: list) -> list:
        known = [s for s in on_day if s["minutes"] is not None]
        items = []
        for i, a in enumerate(known):
            a_start = a["start"].hour * 60 + a["start"].minute
            for b in known[i + 1:]:
                b_start = b["start"].hour * 60 + b["start"].minute
                overlap = min(a_start + a["minutes"], b_start + b["minutes"]) - max(a_start, b_start)
                if overlap > 0:
                    items.append({
                        "kind": "overlap",
                        "id": f"{a['assignment_id']}-{b['assignment_id']}",
                        "first": f"#{a['activity_id']} {a['name']} {_hhmm(a_start)}-{_hhmm(a_start + a['minutes'])}",
                        "second": f"#{b['activity_id']} {b['name']} {_hhmm(b_start)}-{_hhmm(b_start + b['minutes'])}",
                        "activity_ids": [a["activity_id"], b["activity_id"]],
                        "overlap_minutes": overlap,
                    })
        return items

    def _free_slots(self, on_day: list, day: str, slot_hours: float) -> list:
        length = round(slot_hours * 60)
        busy = []
        for s in on_day:
            start = s["start"].hour * 60 + s["start"].minute
            # An unreadable duration blocks the rest of the day, so no proposal can clash with it.
            busy.append((start, start + s["minutes"] if s["minutes"] is not None else DAY_END))
        busy.sort()
        items, cursor = [], DAY_START
        for start, end in busy + [(DAY_END, DAY_END)]:
            if start - cursor >= length and len(items) < MAX_SLOTS:
                slot_end = cursor + length
                items.append({"kind": "free_slot", "id": f"slot-{len(items) + 1}", "date": day,
                              "start": _hhmm(cursor), "end": _hhmm(slot_end),
                              "proposal": f"Proposed: free {slot_hours:g}h slot on {day}, "
                                          f"{_hhmm(cursor)}-{_hhmm(slot_end)} (not saved)"})
            cursor = max(cursor, end)
        return items

    def _narrate(self, payload: dict, plan: dict, work: dict, ctx: RunContext) -> tuple[str, str]:
        """Worker LLM narration of the computed result, or a deterministic one if the LLM is unavailable."""
        if work["totals"]["activities"] and work["activity_evidence"]:
            try:
                system = _prompt("worker_prompt.txt")
            except OSError:
                system = None
            if system:
                reply = ctx.llm(
                    f"Task: {self._task_text(payload)}.\n"
                    f"Plan objective: {plan.get('objective')}\n"
                    f"Activity Evidence:\n" + "\n".join(work["activity_evidence"]) + "\n"
                    f"Computed Results (Python, authoritative): {work['summary']}\n"
                    f"{json.dumps(work['items'])}",
                    system=system, json_mode=False,
                )
                if isinstance(reply, str) and reply.strip():
                    return reply.strip(), "llm"
        limits = [p["text"] for p in work["problems"]]
        if work["totals"]["evidence_truncated"]:
            limits.append(f"Evidence shortened to {MAX_EVIDENCE_LINES} lines.")
        missing = [e["detail"] for e in work["evidence"] if e["status"] == "no_data"]
        output = "No activities found." if not work["totals"]["activities"] else work["summary"]
        used = "; ".join(f"[MCP:{e['source']}] {e.get('count', 0)} record(s)"
                         for e in work["evidence"] if e["status"] == "ok") or "None"
        return (f"Output:\n{output}\nEvidence Used:\n{used}\n"
                f"Limitations:\n{' '.join(missing + limits) or 'None'}"), "fallback"

    # ---- deterministic checks (used by the reviewer and its fallback) --------
    def checks(self, work: dict) -> list[dict]:
        found = []
        for source in work["evidence"]:
            if source["status"] == "no_data":
                found.append({"code": "no_data", "text": source["detail"]})
        found += work["problems"]

        totals = work["totals"]
        overlaps = [i for i in work["items"] if i["kind"] == "overlap"]
        slots = [i for i in work["items"] if i["kind"] == "free_slot"]
        if not found and not totals["activities"]:
            found.append({"code": "no_records", "text": "The activity database has no activities."})
        elif totals["activities"] and not totals["scheduled_on_day"]:
            found.append({"code": "no_records", "text": f"No activities are scheduled on {totals['date']}."})
        for item in overlaps:
            found.append({"code": "clash", "text": f"{item['first']} overlaps {item['second']} "
                                                   f"by {item['overlap_minutes']} min."})
        if not slots:
            found.append({"code": "no_free_slot",
                          "text": f"No free {totals['slot_hours']:g}h slot between {_hhmm(DAY_START)} and "
                                  f"{_hhmm(DAY_END)} on {totals['date']}."})
        for item in slots:  # self-check: a proposal must never clash with a scheduled activity
            start, end = (int(t[:2]) * 60 + int(t[3:]) for t in (item["start"], item["end"]))
            for busy_start, minutes in work["busy"]:
                b_start = int(busy_start[:2]) * 60 + int(busy_start[3:])
                b_end = b_start + minutes if minutes is not None else DAY_END
                if start < b_end and b_start < end:
                    found.append({"code": "proposal_conflict",
                                  "text": f"{item['id']} {item['start']}-{item['end']} clashes with an "
                                          f"activity at {busy_start}."})

        narrative = work["worker_output"]
        unknown = sorted({m.group(1) for m in ID_MENTION.finditer(narrative)} - set(work["known_ids"]), key=int)
        if unknown:
            found.append({"code": "unsupported_claim",
                          "text": f"Worker output cites id(s) {', '.join('#' + i for i in unknown)} that are "
                                  f"not in the evidence."})
        if work["worker_output_source"] == "llm" and totals["activities"] and "no activities found" in narrative.lower():
            found.append({"code": "unsupported_claim",
                          "text": f"Worker output says no activities were found, but the evidence has "
                                  f"{totals['activities']}."})
        if any(heading not in narrative for heading in WORKER_HEADINGS):
            found.append({"code": "format", "text": "Worker output is missing the Output / Evidence Used / "
                                                    "Limitations sections."})
        if len(narrative.split()) > WORKER_WORDS:
            found.append({"code": "too_long", "text": f"Worker output is {len(narrative.split())} words "
                                                      f"(limit {WORKER_WORDS})."})
        return found

    # ---- review -------------------------------------------------------------
    def reviewer_prompt(self, payload: dict, plan: dict, work: dict) -> str:
        _REVIEW_CONTEXT.work = work
        rules = self.fallback_review(payload, plan, work)
        floor = (f"Risk MUST be at least \"{rules['risk']}\" and the recommendation at least as strict as "
                 f"\"{rules['recommendation']}\".\n" if rules["risk"] != "low" else "")
        return (
            f"{_prompt('reviewer_prompt.txt')}\n\n{floor}"
            f"Plan: {json.dumps({'objective': plan.get('objective'), 'steps': plan.get('steps')})}\n"
            f"Worker output:\n{work['worker_output']}\n"
            f"Totals: {json.dumps(work['totals'])}\n"
            f"Computed Checks: {json.dumps([c['text'] for c in work['checks']])}"
        )

    def validate_review(self, review) -> dict:
        """Ground the LLM review in the computed checks (whatever this returns becomes the review).

        Findings are always the computed checks, risk and recommendation are never more lenient than
        the rules, and LLM text citing ids outside the evidence is dropped. The contract does not pass
        `work` here, so reviewer_prompt() leaves it in a per-request thread-local (each run's
        Planner -> Reviewer happens in one request thread).
        """
        review = super().validate_review(review)
        if review.get("recommendation") not in DECISIONS:
            raise ValueError(f"recommendation must be one of {DECISIONS}")
        for key in ("correction", "retest"):
            if not isinstance(review.get(key, ""), str):
                raise ValueError(f"{key} must be a string")
        work = getattr(_REVIEW_CONTEXT, "work", None)
        if work is None:  # called outside a run (e.g. a unit test): shape checks only
            return {**review, "correction": review.get("correction", ""), "retest": review.get("retest", "")}

        rules = self.fallback_review({}, {}, work)
        known = set(work["known_ids"])

        def grounded(text):
            return isinstance(text, str) and {m.group(1) for m in ID_MENTION.finditer(text)} <= known

        # Small models echo the decision values back as "recommendations"; those are not advice.
        kept = [rec for rec in review["recommendations"]
                if grounded(rec) and rec.strip().rstrip(".") not in DECISIONS and len(rec.split()) > 2]
        return {
            "risk": max(review["risk"], rules["risk"], key=RANK.__getitem__),
            "findings": rules["findings"],
            "recommendations": kept or rules["recommendations"],
            "correction": rules["correction"] or (review.get("correction", "") if grounded(review.get("correction", "")) else ""),
            "retest": rules["retest"] or (review.get("retest", "") if grounded(review.get("retest", "")) else ""),
            "recommendation": max(review["recommendation"], rules["recommendation"], key=DECISIONS.index),
        }

    def fallback_review(self, payload: dict, plan: dict, work: dict) -> dict:
        checks = work["checks"]
        codes = {c["code"] for c in checks}
        risk = max((CHECK_LEVELS[c["code"]] for c in checks), key=RANK.__getitem__, default="low")
        if risk == "high":
            recommendation = "Reject"
        elif codes & PARTIAL:
            recommendation = "Partially Accept"
        else:
            recommendation = "Accept"

        corrections, recs = [], []
        unsupported = next((c for c in checks if c["code"] == "unsupported_claim"), None)
        if unsupported:
            corrections.append("Remove the ids that are not in the evidence from the worker output.")
        if "format" in codes or "too_long" in codes:
            corrections.append(f"Rewrite the worker output as Output / Evidence Used / Limitations, "
                               f"at most {WORKER_WORDS} words.")
        for check in checks:
            if check["code"] == "unknown_duration":
                recs.append(check["text"].split(" has a duration")[0] + ": set a duration like '2 hours'.")
        if "clash" in codes:
            recs.append("Move one activity of each clashing pair to another time (Edit on the Activities page).")
        if "no_free_slot" in codes:
            recs.append("Try a shorter slot or another day.")
        if "no_records" in codes:
            recs.append("Add or schedule activities (Register activity, or Edit on the Activities page), then re-run.")
        if "no_data" in codes:
            recs.append("Start the MCP server (7001) and the activity database (6003), then re-run.")
        if "proposal_conflict" in codes:
            recs.append("Do not use the proposed slots; report the clash as a workflow bug.")

        retest = ("Re-run the workflow once the evidence sources are reachable." if "no_data" in codes
                  else "Re-run the workflow after the correction." if corrections else "")
        return {"risk": risk, "findings": [c["text"] for c in checks], "recommendations": recs + corrections,
                "correction": " ".join(corrections), "retest": retest, "recommendation": recommendation}


WORKFLOW = ActivityScheduleCheckWorkflow
