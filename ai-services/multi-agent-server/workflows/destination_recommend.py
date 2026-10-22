"""destination_recommend - Destination (Student 1) workflow for the shared Multi-Agent Server.

"Given some interests, an optional daily budget, trip length and country, which destinations
are worth a human's attention?"

Boundaries (no overlap with other features):
- Read-only, MCP-only: the single allowlisted tool is list_destinations, which reads the
  Destination Database HTTP API. No RAG, and no write tool exists for this workflow.
- Never creates, edits or deletes a destination, and never chooses one for the traveller. The
  output is a ranked recommendation with scores, budget/interest flags and evidence; a human
  decides what (if anything) happens next, and this workflow never writes to destination-db.
All arithmetic/ranking happens in Python; the LLM only plans which sources to read and phrases
the review.
"""
import json
import re
import threading

from core.workflow import RunContext, Workflow

MAX_COUNTRY = 100
MAX_DAILY_COST = 10000
MAX_TRIP_DAYS = 60
MAX_INTERESTS = 10
MAX_INTEREST_LEN = 40
MAX_ITEMS = 5

# tool -> (feature it reads, why it is useful).
SOURCES = {
    "list_destinations": ("Destination", "candidate destinations with their average daily cost, "
                          "recommended trip length, travel style and categories"),
}

# A review recommendation must stay about interests/budget/trip length, never write or choose.
WRITE_WORDS = re.compile(
    r"\b(create|add|update|edit|modify|delete|remove|insert|book|reserve|choose|pick|go with|select)\b",
    re.IGNORECASE)

# reviewer_prompt() -> validate_review() hand-off for the current request thread (see validate_review).
_REVIEW_CONTEXT = threading.local()


def _money(value) -> float:
    return round(float(value or 0), 2)


def _amounts_in(text: str) -> set[float]:
    """Dollar amounts mentioned in text, rounded to whole dollars ('~$60', '$59.50' -> 60)."""
    return {round(float(m)) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text or "")}


class DestinationRecommendWorkflow(Workflow):
    name = "destination_recommend"
    description = ("Destination: recommend destinations for interests, an optional daily budget, trip "
                   "length and country, for human review (read-only; never writes or chooses).")
    allowed_tools = ["list_destinations"]
    uses_rag = False

    # ---- input --------------------------------------------------------------
    def validate_input(self, payload: dict) -> dict:
        interests = payload.get("interests") or []
        if (not isinstance(interests, list) or len(interests) > MAX_INTERESTS
                or any(not isinstance(i, str) or len(i) > MAX_INTEREST_LEN for i in interests)):
            raise ValueError(f"interests must be a list of at most {MAX_INTERESTS} strings "
                             f"(max {MAX_INTEREST_LEN} chars each)")
        cap = payload.get("max_daily_cost")
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, (int, float))
                                or cap != cap or not 0 < cap <= MAX_DAILY_COST):
            raise ValueError(f"max_daily_cost must be a number greater than 0 (max {MAX_DAILY_COST})")
        days = payload.get("trip_days")
        if days is not None and (isinstance(days, bool) or not isinstance(days, int)
                                 or not 1 <= days <= MAX_TRIP_DAYS):
            raise ValueError(f"trip_days must be a whole number from 1 to {MAX_TRIP_DAYS}")
        country = payload.get("country")
        if country is not None and (not isinstance(country, str) or len(country.strip()) > MAX_COUNTRY):
            raise ValueError(f"country must be a string (max {MAX_COUNTRY} chars)")
        clean = {
            "interests": sorted({i.strip().lower() for i in interests if i.strip()}),
            "max_daily_cost": _money(cap) if cap is not None else None,
            "trip_days": days,
            "country": (country or "").strip() or None,
        }
        if not (clean["interests"] or clean["max_daily_cost"] or clean["trip_days"] or clean["country"]):
            raise ValueError("give at least one of interests, max_daily_cost, trip_days or country")
        return clean

    # ---- plan ---------------------------------------------------------------
    def planner_prompt(self, payload: dict) -> str:
        sources = "\n".join(f"- {tool}: {feature} - {why}" for tool, (feature, why) in SOURCES.items())
        criteria = ", ".join(f"{key}={value}" for key, value in payload.items() if value)
        return (
            f"Recommend destinations for: {criteria}.\n"
            "Choose which evidence sources to read. list_destinations is required.\n"
            f"Sources (all read-only):\n{sources}\n"
            "Do not plan any creating, editing, deleting or choosing of a specific destination.\n"
            'Return JSON: {"steps": [{"tool": "<source>", "reason": "<one sentence>"}], "rationale": "<one sentence>"}'
        )

    def fallback_plan(self, payload: dict) -> dict:
        return {
            "steps": [{"tool": tool, "reason": f"{feature}: {why}"} for tool, (feature, why) in SOURCES.items()],
            "rationale": "deterministic fallback: read every allowed source",
        }

    def validate_plan(self, plan, payload: dict) -> dict:
        if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
            raise ValueError("plan needs a non-empty steps list")
        seen = []
        for step in plan["steps"]:
            tool = step.get("tool") if isinstance(step, dict) else None
            if tool not in SOURCES:
                raise ValueError(f"plan uses '{tool}', which is not an allowed read-only source")
            if not isinstance(step.get("reason"), str) or not step["reason"].strip():
                raise ValueError(f"step '{tool}' needs a reason")
            if tool not in seen:
                seen.append(tool)
        if "list_destinations" not in seen:
            raise ValueError("plan must read list_destinations (the primary candidate source)")
        reasons = {s["tool"]: s["reason"] for s in plan["steps"]}
        # Tool arguments are always built by work() from validated input, never taken from the LLM.
        plan["steps"] = [{"tool": t, "reason": reasons[t]} for t in seen]
        return plan

    # ---- work ---------------------------------------------------------------
    def _score(self, row: dict, payload: dict) -> dict:
        text = " ".join([*(str(c) for c in (row.get("categories") or [])),
                         row.get("travel_style") or "", row.get("description") or ""]).lower()
        matched = [i for i in payload["interests"] if i in text]
        cost = _money(row.get("average_daily_cost"))
        cap = payload["max_daily_cost"]
        within_budget = cap is None or cost <= cap
        trip_length = row.get("recommended_trip_length")
        days = payload["trip_days"]
        fits_length = days is not None and trip_length is not None and abs(trip_length - days) <= 1
        score = round(len(matched) * 2 + (1 if within_budget else -3) + (1 if fits_length else 0), 2)
        nights = days if days is not None else trip_length
        return {
            "destination_id": row.get("destination_id"),
            "city": row.get("city"),
            "country": row.get("country"),
            "average_daily_cost": cost,
            "recommended_trip_length": trip_length,
            "travel_style": row.get("travel_style"),
            "matched_interests": matched,
            "within_budget": within_budget,
            "fits_trip_length": fits_length,
            "estimated_trip_cost": _money(cost * nights) if nights else None,
            "score": score,
            "evidence_ref": (f"destination-db:/destinations/{row.get('destination_id')}"
                             if row.get("destination_id") is not None else None),
        }

    def work(self, payload: dict, plan: dict, ctx: RunContext) -> dict:
        evidence: list[dict] = []
        rows: list[dict] = []
        result = ctx.mcp("list_destinations", {})
        if not isinstance(result, dict) or "error" in result:
            detail = result.get("error") if isinstance(result, dict) else "unexpected result"
            evidence.append({"source": "list_destinations", "feature": "Destination", "status": "no_data",
                             "detail": f"no data from Destination: {detail}"})
        else:
            rows = [r for r in result.get("destinations") or [] if isinstance(r, dict)]
            country = payload["country"]
            considered = [r for r in rows if not country or (r.get("country") or "").lower() == country.lower()]
            evidence.append({"source": "list_destinations", "feature": "Destination", "status": "ok",
                             "count": len(considered),
                             "detail": f"{len(considered)} of {len(rows)} destination(s) considered"
                                       + (f" in {country}" if country else "")})
            rows = considered

        scored = sorted((self._score(row, payload) for row in rows),
                        key=lambda r: (-r["score"], r["average_daily_cost"], r["destination_id"] or 0))
        items = [{"id": row["destination_id"] if row["destination_id"] is not None else idx, **row}
                 for idx, row in enumerate(scored[:MAX_ITEMS], start=1)]

        if not items:
            summary = "No destination matched the criteria. Nothing was recommended."
        else:
            top = items[0]
            cap = payload["max_daily_cost"]
            budget = (f" ({'within' if top['within_budget'] else 'over'} the ${cap:.2f}/day cap)"
                      if cap is not None else "")
            summary = (f"{len(rows)} destination(s) considered, {len(items)} recommended; top pick "
                       f"'{top['city']}, {top['country']}' at ${top['average_daily_cost']:.2f}/day{budget}.")

        work = {
            "items": items,
            "summary": summary,
            "evidence": evidence,
            "totals": {**payload, "candidates_considered": len(rows), "recommended": len(items)},
        }
        work["checks"] = self.checks(work)
        return work

    # ---- deterministic checks (used by the reviewer and its fallback) --------
    def checks(self, work: dict) -> list[dict]:
        items, totals = work["items"], work["totals"]
        found = []
        if not items:
            found.append({"code": "no_candidates", "text": "No destination matched the requested criteria."})
        else:
            top = items[0]
            cap = totals["max_daily_cost"]
            if not top["within_budget"]:
                over = _money(top["average_daily_cost"] - cap)
                found.append({"code": "over_budget", "amount": over,
                              "text": f"Top pick '{top['city']}' is ${top['average_daily_cost']:.2f}/day, "
                                      f"${over:.2f} over the ${cap:.2f} cap."})
            if totals["interests"] and not top["matched_interests"]:
                found.append({"code": "weak_match",
                              "text": f"Top pick '{top['city']}' matches none of the requested interests "
                                      f"({', '.join(totals['interests'])})."})
        for source in work["evidence"]:
            if source["status"] == "no_data":
                found.append({"code": "no_data", "feature": source["feature"], "text": source["detail"]})
        return found

    # ---- review -------------------------------------------------------------
    def reviewer_prompt(self, payload: dict, plan: dict, work: dict) -> str:
        _REVIEW_CONTEXT.work = work
        over = any(c["code"] == "over_budget" for c in work["checks"])
        return (
            "Review this destination recommendation. The checks below are computed facts. Write exactly "
            "one finding per check, restating it; do not add findings that are not in the checks and do "
            "not compute new prices. "
            + ("Risk MUST be \"high\" because the top pick is over the daily budget cap. " if over else "")
            + "Recommendations must be about adjusting the interests, budget cap, trip length or country "
            "only; never recommend creating, editing, deleting or choosing a specific destination.\n"
            f"Totals: {json.dumps(work['totals'])}\n"
            f"Checks: {json.dumps([c['text'] for c in work['checks']])}\n"
            'Return JSON: {"risk": "low|medium|high", "findings": ["..."], "recommendations": ["..."]}'
        )

    def validate_review(self, review) -> dict:
        """Ground the LLM review in the computed checks (whatever this returns becomes the review).

        Findings are always the computed checks, risk is never lower than the computed risk, and
        only recommendations that stay in scope and match the facts are kept. The contract does not
        pass `work` here, so reviewer_prompt() leaves it in a per-request thread-local (each run's
        Planner -> Reviewer happens in one request thread).
        """
        review = super().validate_review(review)
        for rec in review["recommendations"]:
            if not isinstance(rec, str) or WRITE_WORDS.search(rec):
                raise ValueError(f"recommendation outside destination_recommend scope: {rec!r}")
        work = getattr(_REVIEW_CONTEXT, "work", None)
        if work is None:  # called outside a run (e.g. a unit test): shape and scope checks only
            return review
        rules = self.fallback_review({}, {}, work)
        known_amounts = _amounts_in(json.dumps(work["checks"]) + json.dumps(work["totals"]) + json.dumps(rules))
        kept = [rec for rec in review["recommendations"] if _amounts_in(rec) <= known_amounts]
        if any(c["code"] in ("no_candidates", "no_data") for c in work["checks"]):
            kept = []  # nothing to recommend: only the rule-based next step, no generic LLM advice
        rank = {"low": 0, "medium": 1, "high": 2}
        return {
            "risk": max(review["risk"], rules["risk"], key=rank.__getitem__),
            "findings": rules["findings"],
            "recommendations": kept or rules["recommendations"],
        }

    def fallback_review(self, payload: dict, plan: dict, work: dict) -> dict:
        checks = work["checks"]
        codes = {c["code"] for c in checks}
        recs = []
        over = next((c for c in checks if c["code"] == "over_budget"), None)
        if over:
            recs.append(f"Raise the daily budget cap by ${over['amount']:.2f}, or look at a lower-cost country.")
        if "weak_match" in codes:
            recs.append("Broaden or change the requested interests; the top candidates matched none of them.")
        if "no_data" in codes:
            risk = "medium"
            recs = ["Check that the Destination database and shared MCP server are running, then retry."]
        elif "no_candidates" in codes:
            risk = "low"
            recs = ["Widen the daily budget cap or try a different country; nothing matched these criteria."]
        else:
            risk = "high" if "over_budget" in codes else "medium" if codes else "low"
        return {"risk": risk, "findings": [c["text"] for c in checks], "recommendations": recs}


WORKFLOW = DestinationRecommendWorkflow
