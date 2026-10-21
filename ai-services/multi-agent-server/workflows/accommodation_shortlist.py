"""accommodation_shortlist - Accommodation (Student 2) workflow for the shared Multi-Agent Server.

"Given a destination, a nightly budget cap and some interests, which accommodation candidates
are worth a human's attention?"

Boundaries (no overlap with other features):
- Read-only tools only. Never calls create_accommodation or any other write tool.
- Never books, reserves or chooses an accommodation. Output is a ranked shortlist with scores,
  budget/interest flags and evidence; a human decides what (if anything) happens next, and this
  workflow never writes to accommodation-db itself.
- Other sources (Destination) are optional reference evidence only, never part of the shortlist.
All arithmetic/ranking happens in Python; the LLM only plans which sources to read and phrases
the review.
"""
import json
import re
import threading

from core.workflow import RunContext, Workflow

MAX_DESTINATION = 100
MAX_BUDGET_CAP = 100000
MAX_INTERESTS = 10
MAX_INTEREST_LEN = 40
MAX_ITEMS = 5

# tool -> (feature it reads, why it is useful).
SOURCES = {
    "search_accommodations": ("Accommodation", "candidate accommodation filtered by destination, "
                               "budget cap and interests"),
    "get_accommodation_by_destination": ("Accommodation", "supplementary unfiltered listings for "
                                         "the destination, in case the filtered search misses some"),
    "list_destinations": ("Destination", "the destination's average daily cost as a reference"),
}

# A review recommendation must stay about search/budget/interests, never name a choice to make.
BOOKING_WORDS = re.compile(r"\b(book|reserve|stay at|choose|pick|go with|select|switch to)\b", re.IGNORECASE)

# reviewer_prompt() -> validate_review() hand-off for the current request thread (see validate_review).
_REVIEW_CONTEXT = threading.local()


def _money(value) -> float:
    return round(float(value or 0), 2)


def _amounts_in(text: str) -> set[float]:
    """Dollar amounts mentioned in text, rounded to whole dollars ('~$60', '$59.50' -> 60)."""
    return {round(float(m)) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text or "")}


class AccommodationShortlistWorkflow(Workflow):
    name = "accommodation_shortlist"
    description = ("Accommodation: shortlist candidate accommodation for a destination against a nightly "
                   "budget cap and interests, for human review (never books or chooses).")
    allowed_tools = ["search_accommodations", "get_accommodation_by_destination", "list_destinations"]
    uses_rag = False

    # ---- input --------------------------------------------------------------
    def validate_input(self, payload: dict) -> dict:
        destination = payload.get("destination")
        if not isinstance(destination, str) or not destination.strip() or len(destination.strip()) > MAX_DESTINATION:
            raise ValueError(f"destination must be a non-empty string (max {MAX_DESTINATION} chars)")
        cap = payload.get("budget_cap")
        if (isinstance(cap, bool) or not isinstance(cap, (int, float)) or not cap > 0
                or cap != cap or cap > MAX_BUDGET_CAP):
            raise ValueError(f"budget_cap must be a number greater than 0 (max {MAX_BUDGET_CAP})")
        interests = payload.get("interests") or []
        if (not isinstance(interests, list) or len(interests) > MAX_INTERESTS
                or any(not isinstance(i, str) or len(i) > MAX_INTEREST_LEN for i in interests)):
            raise ValueError(f"interests must be a list of at most {MAX_INTERESTS} strings "
                             f"(max {MAX_INTEREST_LEN} chars each)")
        return {
            "destination": destination.strip(),
            "budget_cap": _money(cap),
            "interests": sorted({i.strip().lower() for i in interests if i.strip()}),
        }

    # ---- plan ---------------------------------------------------------------
    def planner_prompt(self, payload: dict) -> str:
        sources = "\n".join(f"- {tool}: {feature} - {why}" for tool, (feature, why) in SOURCES.items())
        interests_text = f" matching interests: {', '.join(payload['interests'])}" if payload["interests"] else ""
        return (
            f"Shortlist accommodation in {payload['destination']} within a ${payload['budget_cap']:.2f}/night "
            f"cap{interests_text}.\n"
            "Choose which evidence sources to read. search_accommodations is required.\n"
            f"Sources (all read-only):\n{sources}\n"
            "Do not plan any booking, reserving or choosing of a specific accommodation.\n"
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
        if "search_accommodations" not in seen:
            raise ValueError("plan must read search_accommodations (the primary candidate source)")
        reasons = {s["tool"]: s["reason"] for s in plan["steps"]}
        # Tool arguments are always built by work() from validated input, never taken from the LLM.
        plan["steps"] = [{"tool": t, "reason": reasons[t]} for t in seen]
        return plan

    # ---- work ---------------------------------------------------------------
    def _read(self, ctx: RunContext, tool: str, args: dict, evidence: list) -> dict | None:
        feature = SOURCES[tool][0]
        result = ctx.mcp(tool, args)
        if not isinstance(result, dict) or "error" in result:
            detail = result.get("error") if isinstance(result, dict) else "unexpected result"
            evidence.append({"source": tool, "feature": feature, "status": "no_data",
                             "detail": f"no data from {feature}: {detail}"})
            return None
        return result

    def _score(self, acc: dict, cap: float, interests: list[str]) -> tuple[list[str], bool, float]:
        price = _money(acc.get("price_per_night"))
        text = " ".join(str(a) for a in (acc.get("amenities") or [])) + " " + (acc.get("description") or "")
        text = text.lower()
        matched = [i for i in interests if i in text]
        within_budget = price <= cap
        rating = acc.get("rating") or 0
        score = round(len(matched) * 2 + float(rating) - (0 if within_budget else 3), 2)
        return matched, within_budget, score

    def work(self, payload: dict, plan: dict, ctx: RunContext) -> dict:
        destination, cap, interests = payload["destination"], payload["budget_cap"], payload["interests"]
        planned = [step["tool"] for step in plan["steps"]]
        evidence: list[dict] = []

        candidates: dict = {}
        relaxed = False
        search_args = {"destination": destination, "max_price": cap}
        if interests:
            search_args["amenities"] = ",".join(interests)
        found = self._read(ctx, "search_accommodations", search_args, evidence)
        rows = (found or {}).get("accommodations", []) if found is not None else []
        if found is not None and not rows and interests:
            # ADAPT: the amenities filter was too strict; retry once on budget alone.
            relaxed = True
            found = self._read(ctx, "search_accommodations", {"destination": destination, "max_price": cap}, evidence)
            rows = (found or {}).get("accommodations", []) if found is not None else []
        if found is not None:
            for row in rows:
                candidates[row.get("id") if row.get("id") is not None else row.get("name")] = row
            evidence.append({"source": "search_accommodations", "feature": "Accommodation", "status": "ok",
                             "count": len(rows), "detail": f"{len(rows)} candidate(s) in {destination} at/under "
                             f"${cap:.2f}/night" + (" (amenities filter relaxed: none matched)" if relaxed else "")})

        if "get_accommodation_by_destination" in planned:
            supplementary = self._read(ctx, "get_accommodation_by_destination", {"destination": destination}, evidence)
            if supplementary is not None:
                added = 0
                for row in supplementary.get("accommodations", []):
                    key = row.get("id") if row.get("id") is not None else row.get("name")
                    if key not in candidates:
                        candidates[key] = row
                        added += 1
                evidence.append({"source": "get_accommodation_by_destination", "feature": "Accommodation",
                                 "status": "ok", "count": added,
                                 "detail": f"{added} additional unfiltered listing(s) for {destination}"})

        if "list_destinations" in planned:
            found_dest = self._read(ctx, "list_destinations", {"city": destination}, evidence)
            if found_dest is not None:
                daily = [d.get("average_daily_cost") for d in found_dest.get("destinations", [])
                        if d.get("average_daily_cost")]
                evidence.append({"source": "list_destinations", "feature": "Destination", "status": "ok",
                                 "count": len(daily),
                                 "detail": f"{destination}: average daily cost "
                                 f"{daily[0] if daily else 'n/a'} (reference only)",
                                 "average_daily_cost": daily[0] if daily else None})

        scored = []
        for acc in candidates.values():
            matched, within_budget, score = self._score(acc, cap, interests)
            scored.append({
                "name": acc.get("name"),
                "price_per_night": _money(acc.get("price_per_night")),
                "rating": acc.get("rating"),
                "matched_interests": matched,
                "within_budget": within_budget,
                "score": score,
                "accommodation_id": acc.get("id"),
                "evidence_ref": (f"accommodation-db:/accommodations/{acc.get('id')}"
                                if acc.get("id") is not None else None),
            })
        scored.sort(key=lambda row: row["score"], reverse=True)
        items = [{"id": idx, **row} for idx, row in enumerate(scored[:MAX_ITEMS], start=1)]

        if not items:
            summary = f"No accommodation found in {destination} within ${cap:.2f}/night. Nothing was shortlisted."
        else:
            top = items[0]
            summary = (f"{destination}: {len(candidates)} candidate(s) considered, {len(items)} shortlisted; "
                      f"top pick '{top['name']}' at ${top['price_per_night']:.2f}/night "
                      f"({'within' if top['within_budget'] else 'over'} the ${cap:.2f} cap).")

        work = {
            "items": items,
            "summary": summary,
            "evidence": evidence,
            "totals": {"budget_cap": cap, "interests": interests,
                      "candidates_considered": len(candidates), "shortlisted": len(items)},
        }
        work["checks"] = self.checks(work)
        return work

    # ---- deterministic checks (used by the reviewer and its fallback) --------
    def checks(self, work: dict) -> list[dict]:
        items, totals = work["items"], work["totals"]
        found = []
        if not items:
            found.append({"code": "no_candidates",
                          "text": f"No accommodation matched destination and budget cap ${totals['budget_cap']:.2f}."})
        else:
            top = items[0]
            if not top["within_budget"]:
                over = _money(top["price_per_night"] - totals["budget_cap"])
                found.append({"code": "over_budget", "amount": over,
                              "text": f"Top pick '{top['name']}' is ${top['price_per_night']:.2f}/night, "
                                      f"${over:.2f} over the ${totals['budget_cap']:.2f} cap."})
            if totals["interests"] and not top["matched_interests"]:
                found.append({"code": "weak_match",
                              "text": f"Top pick '{top['name']}' matches none of the requested interests "
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
            "Review this accommodation shortlist. The checks below are computed facts. Write exactly one "
            "finding per check, restating it; do not add findings that are not in the checks and do not "
            "compute new prices. "
            + ("Risk MUST be \"high\" because the top pick is over the budget cap. " if over else "")
            + "Recommendations must be about narrowing the search, or adjusting the budget cap or interests "
            "only; never recommend booking, reserving or choosing a specific accommodation.\n"
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
            if not isinstance(rec, str) or BOOKING_WORDS.search(rec):
                raise ValueError(f"recommendation outside accommodation_shortlist scope: {rec!r}")
        work = getattr(_REVIEW_CONTEXT, "work", None)
        if work is None:  # called outside a run (e.g. a unit test): shape and scope checks only
            return review
        rules = self.fallback_review({}, {}, work)
        known_amounts = _amounts_in(json.dumps(work["checks"]) + json.dumps(work["totals"]) + json.dumps(rules))
        kept = [rec for rec in review["recommendations"] if _amounts_in(rec) <= known_amounts]
        if any(c["code"] == "no_candidates" for c in work["checks"]):
            kept = []  # nothing to shortlist: only the rule-based next step, no generic LLM advice
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
            recs.append(f"Raise the budget cap by ${over['amount']:.2f}, or search a lower price band.")
        if "weak_match" in codes:
            recs.append("Broaden or change the requested interests; the top candidates matched none of them.")
        risk = "high" if "over_budget" in codes else "medium" if codes - {"no_candidates"} else "low"
        if "no_candidates" in codes:
            risk = "low"
            recs = ["Widen the budget cap or try a nearby destination; nothing matched this search."]
        return {"risk": risk, "findings": [c["text"] for c in checks], "recommendations": recs}


WORKFLOW = AccommodationShortlistWorkflow
