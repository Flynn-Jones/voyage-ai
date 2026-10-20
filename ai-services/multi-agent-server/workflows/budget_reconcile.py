"""budget_reconcile - Budget (Student 4) workflow for the shared Multi-Agent Server.

"Given what has already been chosen for this trip, what will it cost, does it fit my cap,
and which budget lines should I record?"

Boundaries (no overlap with other features):
- Read-only tools only. Never calls create_accommodation or any write tool.
- Never recommends or chooses destinations, hotels, activities or schedules. Only costs that
  are ALREADY chosen in Itinerary become draft budget lines; Accommodation, Destination and
  Activity data is reference evidence. Outputs are amounts, categories, variances, warnings
  and budget lines.
- Out-of-scope requests in the note (e.g. "book a cheaper hotel") are declined and not acted on.
- Other features' data is optional: a missing or failing tool becomes "no data from <feature>".
All arithmetic happens in Python; the LLM only plans the evidence and phrases the review.
"""
import json
import re
import threading
from collections import Counter
from difflib import SequenceMatcher

from core.workflow import RunContext, Workflow

MAX_NOTE = 500
MAX_TRIP_REFERENCE = 120

# tool -> (feature it reads, why it is useful). "rag" is the shared RAG server, not an MCP tool.
SOURCES = {
    "list_expenses": ("Budget", "budget lines already recorded for this trip"),
    "get_itinerary": ("Itinerary", "items already scheduled for this trip, with their costs"),
    "list_activities": ("Activity", "activity catalogue to categorise scheduled items"),
    "list_destinations": ("Destination", "the destination's average daily cost as a reference"),
    "get_accommodation_by_destination": ("Accommodation", "accommodation price range as a reference"),
    "search_accommodations": ("Accommodation", "accommodation price range (filtered search) as a reference"),
    "rag": ("RAG", "shared budgeting guidance"),
}
EXPECTED_CATEGORIES = ("Transport", "Accommodation", "Activities", "Food")
FOOD_WORDS = ("food", "cooking", "market", "breakfast", "lunch", "dinner", "tasting", "restaurant", "cafe")
STOPWORDS = {"the", "a", "an", "and", "of", "to", "for", "in", "on", "at", "visit", "tour", "local", "pass", "passes"}
RAG_QUERY = "budgeting guidelines: trip expense categories, contingency and cost tracking"

# Requests that would act on another feature's data: declined, never executed.
OUT_OF_SCOPE = re.compile(
    r"\b(book|reserve|cancel|switch|change|swap|find|choose|pick|recommend|suggest)\b.{0,40}"
    r"\b(hotel|accommodation|room|flight|activit(?:y|ies)|tour|destination|itinerary|schedule)",
    re.IGNORECASE,
)
# A review recommendation must stay about amounts/categories, never name a choice to make.
CHOICE_WORDS = re.compile(r"\b(book|reserve|stay at|switch to|choose|pick|instead of|go to)\b", re.IGNORECASE)


# reviewer_prompt() -> validate_review() hand-off for the current request thread (see validate_review).
_REVIEW_CONTEXT = threading.local()


def _money(value) -> float:
    return round(float(value or 0), 2)


def _amounts_in(text: str) -> set[float]:
    """Dollar amounts mentioned in text, rounded to whole dollars ('~$60', '$59.50' -> 60)."""
    return {round(float(m)) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text or "")}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {w.rstrip("s") for w in words if len(w) >= 4 and w not in STOPWORDS}


def similar(a: str, b: str) -> float:
    """0..1 fuzzy name similarity: max of character ratio and shared-keyword overlap."""
    ratio = SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()
    ta, tb = _tokens(a), _tokens(b)
    overlap = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
    return round(max(ratio, overlap), 2)


def city_of(destination_id: str) -> str | None:
    """Budget uses text ids like 'dest-paris' -> 'Paris'."""
    if isinstance(destination_id, str) and destination_id.startswith("dest-"):
        return destination_id[5:].replace("-", " ").title()
    return None


def categorise(name: str, activity_type: str | None = None) -> str:
    text = f"{name} {activity_type or ''}".lower()
    return "Food" if any(word in text for word in FOOD_WORDS) else "Activities"


class BudgetReconcileWorkflow(Workflow):
    name = "budget_reconcile"
    description = ("Budget: reconcile a trip's recorded expenses with already-chosen costs, check the cap, "
                   "and draft budget lines for human approval (amounts and categories only).")
    allowed_tools = ["list_expenses", "get_itinerary", "search_accommodations",
                     "get_accommodation_by_destination", "list_destinations", "list_activities"]
    uses_rag = True

    # ---- input --------------------------------------------------------------
    def validate_input(self, payload: dict) -> dict:
        trip = payload.get("trip_reference")
        if not isinstance(trip, str) or not trip.strip() or len(trip.strip()) > MAX_TRIP_REFERENCE:
            raise ValueError(f"trip_reference must be a non-empty string (max {MAX_TRIP_REFERENCE} chars)")
        cap = payload.get("budget_cap")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not cap > 0 or cap != cap:
            raise ValueError("budget_cap must be a number greater than 0")
        note = payload.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise ValueError(f"note must be a string of at most {MAX_NOTE} characters")
        declined = [m.group(0) for m in OUT_OF_SCOPE.finditer(note)]
        return {"trip_reference": trip.strip(), "budget_cap": _money(cap), "note": note.strip(),
                "declined_requests": declined}

    # ---- plan ---------------------------------------------------------------
    def planner_prompt(self, payload: dict) -> str:
        sources = "\n".join(f"- {tool}: {feature} - {why}" for tool, (feature, why) in SOURCES.items())
        return (
            f"Trip {payload['trip_reference']} has a budget cap of ${payload['budget_cap']:.2f}.\n"
            "Choose which evidence sources to read to reconcile its costs. list_expenses is required.\n"
            f"Sources (all read-only):\n{sources}\n"
            "Do not plan any booking, choosing or changing of hotels, destinations, activities or schedules.\n"
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
        if "list_expenses" not in seen:
            raise ValueError("plan must read list_expenses (Budget's own records)")
        reasons = {s["tool"]: s["reason"] for s in plan["steps"]}
        if "get_itinerary" in seen and "list_activities" not in seen:
            # Declared dependency, not a choice: scheduled items are categorised with the Activity catalogue.
            seen.append("list_activities")
            reasons["list_activities"] = "added automatically: categorises the scheduled itinerary items"
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

    def work(self, payload: dict, plan: dict, ctx: RunContext) -> dict:
        trip, cap = payload["trip_reference"], payload["budget_cap"]
        planned = [step["tool"] for step in plan["steps"]]
        evidence: list[dict] = []

        # Budget's own lines (required). Cancelled lines are ignored.
        existing, cancelled = [], []
        expenses = self._read(ctx, "list_expenses", {"trip_reference": trip}, evidence)
        for row in (expenses or {}).get("expenses", []):
            if row.get("trip_reference") not in (None, trip):
                continue
            (cancelled if row.get("status") == "Cancelled" else existing).append(row)
        if expenses is not None:
            evidence.append({"source": "list_expenses", "feature": "Budget", "status": "ok", "count": len(existing),
                             "detail": f"{len(existing)} active line(s); {len(cancelled)} cancelled line(s) ignored"})

        dest_counts = Counter(r.get("destination_id") for r in existing if r.get("destination_id"))
        trip_destination = dest_counts.most_common(1)[0][0] if dest_counts else None
        city = city_of(trip_destination)

        # Activity catalogue (optional): id -> type, only to categorise itinerary items.
        activity_types = {}
        if "list_activities" in planned:
            catalogue = self._read(ctx, "list_activities", {}, evidence)
            if catalogue is not None:
                for act in catalogue.get("activities", []):
                    activity_types[act.get("id") or act.get("activity_id")] = act.get("activity_type")
                evidence.append({"source": "list_activities", "feature": "Activity", "status": "ok",
                                 "count": len(activity_types), "detail": "used only to categorise scheduled items"})

        # Itinerary: the only source of new lines (costs already chosen by the traveller).
        candidates = []
        if "get_itinerary" in planned:
            itinerary = self._read(ctx, "get_itinerary", {"trip_reference": trip}, evidence)
            if itinerary is not None:
                rows = itinerary.get("items", [])
                evidence.append({"source": "get_itinerary", "feature": "Itinerary", "status": "ok",
                                 "count": len(rows), "detail": f"{len(rows)} scheduled item(s)"})
                for row in rows:
                    item_id = row.get("itinerary_item_id") or row.get("id")
                    name = row.get("notes") or f"Itinerary item {item_id}"
                    candidates.append({
                        "expense": name,
                        "category": categorise(name, activity_types.get(row.get("activity_id"))),
                        "estimated_cost": _money(row.get("estimated_cost")),
                        "destination_id": trip_destination or f"itinerary-destination-{row.get('destination_id')}",
                        "source": "itinerary",
                        "evidence_ref": f"itinerary-db:/itinerary-items/{item_id}",
                    })

        # Reference evidence only (never turned into lines).
        if "list_destinations" in planned:
            if city:
                found = self._read(ctx, "list_destinations", {"city": city}, evidence)
                if found is not None:
                    daily = [d.get("average_daily_cost") for d in found.get("destinations", []) if d.get("average_daily_cost")]
                    evidence.append({"source": "list_destinations", "feature": "Destination", "status": "ok",
                                     "count": len(daily), "detail": f"{city}: average daily cost {daily[0] if daily else 'n/a'}",
                                     "average_daily_cost": daily[0] if daily else None})
            else:
                evidence.append({"source": "list_destinations", "feature": "Destination", "status": "no_data",
                                 "detail": "no data from Destination: trip has no destination on record"})
        for tool in ("get_accommodation_by_destination", "search_accommodations"):
            if tool in planned:
                if not city:
                    evidence.append({"source": tool, "feature": "Accommodation", "status": "no_data",
                                     "detail": "no data from Accommodation: trip has no destination on record"})
                    continue
                found = self._read(ctx, tool, {"destination": city}, evidence)
                if found is not None:
                    prices = sorted(_money(a.get("price_per_night")) for a in found.get("accommodations", [])
                                    if a.get("price_per_night") is not None)
                    detail = (f"{city}: {len(prices)} listing(s), ${prices[0]:.2f}-${prices[-1]:.2f}/night"
                              if prices else f"{city}: no priced listings")
                    evidence.append({"source": tool, "feature": "Accommodation", "status": "ok",
                                     "count": len(prices), "detail": detail + " (reference only)"})

        if "rag" in planned:
            rag = ctx.rag(RAG_QUERY, k=3)
            hits = rag.get("results") or []
            status = "ok" if rag.get("status") == "success" and hits else "no_data"
            detail = (f"{len(hits)} guidance snippet(s): " + "; ".join(h.get("source_id", "?") for h in hits[:3])
                      if status == "ok" else f"no data from RAG: {rag.get('error') or 'no budgeting guidance indexed'}")
            evidence.append({"source": "rag", "feature": "RAG", "status": status, "count": len(hits), "detail": detail})

        # Drop candidates Budget already has (near-identical name in the same category).
        items, skipped = [], []
        for cand in candidates:
            match = next((r for r in existing if r.get("category") == cand["category"]
                          and SequenceMatcher(None, r["expense"].lower(), cand["expense"].lower()).ratio() >= 0.9), None)
            if match:
                skipped.append(f"{cand['expense']} (already recorded as expense {match.get('id')})")
                continue
            items.append({"id": len(items) + 1, **cand})

        # ---- arithmetic (Python only) ----
        existing_estimated = _money(sum(_money(r.get("estimated_cost")) for r in existing))
        existing_actual = _money(sum(_money(r.get("actual_cost")) for r in existing if r.get("actual_cost") is not None))
        proposed_additions = _money(sum(i["estimated_cost"] for i in items))
        projected_total = _money(existing_estimated + proposed_additions)
        remaining_or_over = _money(cap - projected_total)

        breakdown: dict[str, dict] = {}
        for row in existing:
            entry = breakdown.setdefault(row.get("category") or "Uncategorised", {"existing": 0.0, "proposed": 0.0})
            entry["existing"] = _money(entry["existing"] + _money(row.get("estimated_cost")))
        for item in items:
            entry = breakdown.setdefault(item["category"], {"existing": 0.0, "proposed": 0.0})
            entry["proposed"] = _money(entry["proposed"] + item["estimated_cost"])
        for entry in breakdown.values():
            entry["total"] = _money(entry["existing"] + entry["proposed"])

        if not existing and not items:
            summary = f"No evidence found for {trip}: no budget lines and no scheduled items. Nothing was drafted."
        else:
            state = f"${abs(remaining_or_over):.2f} {'remaining' if remaining_or_over >= 0 else 'over'}"
            summary = (f"{trip}: projected ${projected_total:.2f} against a ${cap:.2f} cap ({state}); "
                       f"{len(items)} draft line(s) totalling ${proposed_additions:.2f}.")
        work = {
            "items": items,
            "summary": summary,
            "evidence": evidence,
            "totals": {"budget_cap": cap, "existing_estimated": existing_estimated, "existing_actual": existing_actual,
                       "proposed_additions": proposed_additions, "projected_total": projected_total,
                       "remaining_or_over": remaining_or_over},
            "category_breakdown": breakdown,
            "existing_lines": [{"id": r.get("id"), "expense": r.get("expense"), "category": r.get("category"),
                                "estimated_cost": _money(r.get("estimated_cost")),
                                "actual_cost": None if r.get("actual_cost") is None else _money(r.get("actual_cost")),
                                "status": r.get("status")} for r in existing],
            "ignored_cancelled": [r.get("expense") for r in cancelled],
            "skipped_already_recorded": skipped,
            "declined_requests": payload.get("declined_requests", []),
        }
        work["checks"] = self.checks(work)
        return work

    # ---- deterministic checks (used by the reviewer and its fallback) --------
    def checks(self, work: dict) -> list[dict]:
        totals, items, existing = work["totals"], work["items"], work["existing_lines"]
        found = []
        if totals["remaining_or_over"] < 0:
            found.append({"code": "overspend", "amount": abs(totals["remaining_or_over"]),
                          "text": f"Projected ${totals['projected_total']:.2f} is ${abs(totals['remaining_or_over']):.2f} "
                                  f"over the ${totals['budget_cap']:.2f} cap."})
        for row in existing:
            if row["actual_cost"] is not None and row["actual_cost"] > row["estimated_cost"]:
                diff = _money(row["actual_cost"] - row["estimated_cost"])
                found.append({"code": "actual_above_estimate", "category": row["category"], "amount": diff,
                              "text": f"{row['category']}: '{row['expense']}' actual ${row['actual_cost']:.2f} is "
                                      f"${diff:.2f} above its ${row['estimated_cost']:.2f} estimate."})
        for item in items:
            for row in existing:
                score = similar(item["expense"], row["expense"])
                if row["category"] == item["category"] and score >= 0.5:
                    found.append({"code": "possible_duplicate", "category": item["category"], "item_id": item["id"],
                                  "amount": item["estimated_cost"],
                                  "text": f"{item['category']}: draft line {item['id']} '{item['expense']}' may duplicate "
                                          f"recorded '{row['expense']}' (similarity {score})."})
        present = {row["category"] for row in existing} | {item["category"] for item in items}
        if existing or items:
            for category in EXPECTED_CATEGORIES:
                if category not in present:
                    found.append({"code": "missing_category", "category": category,
                                  "text": f"No {category} budget recorded or drafted."})
        for item in items:
            if not item.get("evidence_ref"):
                found.append({"code": "no_evidence", "item_id": item["id"], "text": f"Draft line {item['id']} has no evidence."})
        for source in work["evidence"]:
            if source["status"] == "no_data":
                found.append({"code": "no_data", "feature": source["feature"], "text": source["detail"]})
        if not existing and not items:
            found.append({"code": "no_evidence_found", "text": work["summary"]})
        for request in work.get("declined_requests", []):
            found.append({"code": "declined", "text": f"Declined out-of-scope request '{request}': Budget only "
                                                       "reconciles costs and never books or changes other features' data."})
        return found

    # ---- review -------------------------------------------------------------
    def reviewer_prompt(self, payload: dict, plan: dict, work: dict) -> str:
        _REVIEW_CONTEXT.work = work
        over = any(c["code"] == "overspend" for c in work["checks"])
        return (
            "Review this trip budget reconciliation. The checks below are computed facts. Write exactly one "
            "finding per check, restating it; do not add findings that are not in the checks and do not compute "
            "new numbers. "
            + ("Risk MUST be \"high\" because the projected total is over the cap. " if over else "")
            + "Recommendations must be about amounts and categories only (for example 'reduce Activities "
            "by ~$60'); never recommend booking, choosing or changing a hotel, destination, activity or schedule.\n"
            f"Totals: {json.dumps(work['totals'])}\n"
            f"Category breakdown: {json.dumps(work['category_breakdown'])}\n"
            f"Checks: {json.dumps([c['text'] for c in work['checks']])}\n"
            'Return JSON: {"risk": "low|medium|high", "findings": ["..."], "recommendations": ["..."]}'
        )

    def validate_review(self, review) -> dict:
        """Ground the LLM review in the computed checks (whatever this returns becomes the review).

        Findings are always the computed checks, risk is never lower than the computed risk, and
        only recommendations that stay in Budget's scope and match the facts are kept. The contract
        does not pass `work` here, so reviewer_prompt() leaves it in a per-request thread-local
        (each run's Planner -> Reviewer happens in one request thread).
        """
        review = super().validate_review(review)
        for rec in review["recommendations"]:
            if not isinstance(rec, str) or CHOICE_WORDS.search(rec):
                raise ValueError(f"recommendation outside Budget scope: {rec!r}")
        work = getattr(_REVIEW_CONTEXT, "work", None)
        if work is None:  # called outside a run (e.g. a unit test): shape and scope checks only
            return review
        rules = self.fallback_review({}, {}, work)
        over = any(c["code"] == "overspend" for c in work["checks"])
        known_amounts = _amounts_in(json.dumps(work["checks"]) + json.dumps(work["totals"]) + json.dumps(rules))
        kept = [rec for rec in review["recommendations"]
                if (over or not re.match(r"\s*(reduce|cut|lower)\b", rec, re.IGNORECASE))
                and _amounts_in(rec) <= known_amounts]
        if any(c["code"] == "no_evidence_found" for c in work["checks"]):
            kept = []  # nothing to reconcile: only the rule-based next step, no generic LLM advice
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
        over = next((c for c in checks if c["code"] == "overspend"), None)
        if over:
            biggest = max(work["category_breakdown"].items(), key=lambda kv: kv[1]["proposed"], default=None)
            if biggest and biggest[1]["proposed"] >= over["amount"]:
                recs.append(f"Reduce {biggest[0]} by ~${over['amount']:.0f} (largest draft category, "
                            f"${biggest[1]['proposed']:.2f} proposed) or raise the cap by ${over['amount']:.2f}.")
            elif biggest and biggest[1]["proposed"] > 0:
                rest = _money(over["amount"] - biggest[1]["proposed"])
                recs.append(f"Draft lines alone cannot close the ${over['amount']:.2f} gap: reduce {biggest[0]} by up to "
                            f"${biggest[1]['proposed']:.2f} and recorded spending by ~${rest:.0f}, "
                            f"or raise the cap by ${over['amount']:.2f}.")
            else:
                recs.append(f"Reduce recorded spending by ~${over['amount']:.0f} or raise the cap by ${over['amount']:.2f}.")
        for c in checks:
            if c["code"] == "actual_above_estimate":
                recs.append(f"Increase the {c['category']} estimate by ${c['amount']:.2f} to match the actual cost.")
            elif c["code"] == "possible_duplicate":
                recs.append(f"Check {c['category']} draft line {c['item_id']} (${c['amount']:.2f}) before approving; "
                            "it may already be covered.")
            elif c["code"] == "missing_category":
                recs.append(f"Add a {c['category']} budget amount.")
        risk = "high" if "overspend" in codes else "medium" if codes - {"declined"} else "low"
        if "no_evidence_found" in codes:
            risk = "low"
            recs = ["Record budget lines or schedule itinerary items for this trip, then reconcile again."]
        return {"risk": risk, "findings": [c["text"] for c in checks], "recommendations": recs}


WORKFLOW = BudgetReconcileWorkflow
