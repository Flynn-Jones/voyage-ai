#!/usr/bin/env python3
"""Run a real tagged create -> read -> update -> delete against every feature on the ALB.

`smoke-test.sh`'s `read:` checks only prove the seeded data can be READ. The Release 2
cloud review additionally wants a complete CRUD lifecycle per feature, evidenced in
docs/release-2/cloud/crud-lifecycle-evidence-<UTC>.json (format
`voyageai-crud-lifecycle/1`, read by ai-services/agentic_loop's cloud_collector). This
script performs that lifecycle for real over HTTP and writes that file - it never invents
a result: a step that does not behave is recorded FAIL with the reason.

Every record it creates is tagged `r2-crud-<UTC>` and deleted again at the end of its own
lifecycle, so a successful run leaves the deployment exactly as it found it. The delete
step is only PASS when the follow-up read confirms the record is really gone.

    python3 scripts/deploy/aws/crud-lifecycle.py --alb-url http://<alb dns>
    python3 scripts/deploy/aws/crud-lifecycle.py --alb-url http://<alb dns> --dry-run

Requires `requests`. The ALB URL must be the one in the smoke run's stack-outputs file,
or the review will not bind this evidence to the reviewed deployment.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CLOUD_DIR = REPO_ROOT / "docs" / "release-2" / "cloud"
LIFECYCLE_FORMAT = "voyageai-crud-lifecycle/1"
STEPS = ("create", "read", "update", "delete")
TIMEOUT = 30

PORTS = {
    "destination-service": 3001,
    "accommodation-service": 3002,
    "activity-service": 3003,
    "budget-service": 3004,
    "itinerary-service": 3005,
}


class StepFailure(Exception):
    """A lifecycle step did not behave; the message becomes the recorded reason."""


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Feature:
    """One feature's lifecycle. Subclasses implement the four steps.

    Each step returns a short note on success and raises StepFailure otherwise. `self.ident`
    is the created record's id, discovered by create().
    """

    name = ""

    def __init__(self, base, tag, log):
        self.base = f"{base}:{PORTS[self.name]}"
        self.tag = tag
        self.log = log
        self.ident = None

    # -- helpers ---------------------------------------------------------
    def req(self, method, path, **kw):
        url = f"{self.base}{path}"
        kw.setdefault("timeout", TIMEOUT)
        kw.setdefault("allow_redirects", False)
        try:
            r = requests.request(method, url, **kw)  # noqa: S113 - kw always carries a timeout (setdefault above)
        except requests.RequestException as exc:
            raise StepFailure(f"{method} {path} failed: {exc}") from exc
        self.log(f"      {method} {path} -> {r.status_code}")
        return r

    def expect(self, r, codes, what):
        if r.status_code not in codes:
            raise StepFailure(f"{what}: expected HTTP {'/'.join(map(str, codes))}, got {r.status_code}")
        return r

    def html_ids(self, path, pattern):
        """The set of record ids currently linked from a list page."""
        r = self.expect(self.req("GET", path), (200,), "list page")
        return {int(m) for m in re.findall(pattern, r.text)}

    def id_appearing(self, before, after, path, pattern):
        new = after - before
        if len(new) != 1:
            raise StepFailure(
                f"expected exactly 1 new record on {path} after create, saw {sorted(new) or 'none'}")
        return new.pop()

    # -- steps -----------------------------------------------------------
    def create(self):
        raise NotImplementedError

    def read(self):
        raise NotImplementedError

    def update(self):
        raise NotImplementedError

    def delete(self):
        raise NotImplementedError


class FormFeature(Feature):
    """A feature driven through its HTML frontend: the new id is found by diffing the
    list page before and after the create, which works whether or not it redirects."""

    list_path = "/"
    id_pattern = ""
    detail_path = "/{id}"

    def payload(self):
        raise NotImplementedError

    def create_path(self):
        raise NotImplementedError

    def create(self):
        before = self.html_ids(self.list_path, self.id_pattern)
        r = self.req("POST", self.create_path(), data=self.payload())
        self.expect(r, (200, 201, 302, 303), "create")
        after = self.html_ids(self.list_path, self.id_pattern)
        self.ident = self.id_appearing(before, after, self.list_path, self.id_pattern)
        return f"created id {self.ident} via POST {self.create_path()} (HTTP {r.status_code})"

    def read(self):
        path = self.detail_path.format(id=self.ident)
        r = self.expect(self.req("GET", path), (200,), "read")
        if self.tag not in r.text:
            raise StepFailure(f"read {path} did not contain the tag {self.tag}")
        return f"GET {path} returned the tagged record"

    def update_path(self):
        return self.detail_path.format(id=self.ident)

    def update(self):
        marker = f"updated-{self.tag}"
        data = {**self.payload(), "description": marker}
        r = self.req("POST", self.update_path(), data=data)
        self.expect(r, (200, 201, 302, 303), "update")
        check = self.expect(self.req("GET", self.detail_path.format(id=self.ident)), (200,),
                            "read-after-update")
        if marker not in check.text:
            raise StepFailure(f"update did not persist: {marker!r} absent from the record after the write")
        return f"description updated to {marker} and confirmed by a follow-up read"

    def delete_request(self):
        return self.req("POST", f"{self.detail_path.format(id=self.ident)}/delete")

    def delete(self):
        r = self.delete_request()
        self.expect(r, (200, 202, 204, 302, 303), "delete")
        check = self.req("GET", self.detail_path.format(id=self.ident))
        if check.status_code == 200 and self.tag in check.text:
            raise StepFailure("delete returned success but the record is still readable")
        if check.status_code not in (404, 410, 302, 303):
            raise StepFailure(f"read-after-delete returned HTTP {check.status_code}, expected 404")
        return f"deleted and confirmed gone (read-after-delete HTTP {check.status_code})"


class Destination(FormFeature):
    name = "destination-service"
    id_pattern = r'href="/destinations/(\d+)"'
    detail_path = "/destinations/{id}"

    def create_path(self):
        return "/destinations/new"

    def update_path(self):
        return f"/destinations/{self.ident}/edit"

    def payload(self):
        return {"city": self.tag, "country": "Testland",
                "description": f"created-{self.tag}", "average_daily_cost": "123.45",
                "recommended_trip_length": "3", "travel_style": "solo",
                "categories": "crud-test"}


class Accommodation(FormFeature):
    name = "accommodation-service"
    id_pattern = r'/accommodations/(\d+)'
    detail_path = "/accommodations/{id}"

    def create_path(self):
        return "/accommodations"

    def payload(self):
        return {"name": self.tag, "destination_id": "dest-tokyo", "destination_city": "Tokyo",
                "type": "hotel", "price_per_night": "150.0", "rating": "4.5",
                "location": "Testville", "latitude": "35.6895", "longitude": "139.6917",
                "amenities": "WiFi", "description": f"created-{self.tag}"}


class Activity(FormFeature):
    name = "activity-service"
    id_pattern = r'id="activity-row-(\d+)"'
    detail_path = "/activities/{id}"

    def create_path(self):
        return "/activities/register"

    def update_path(self):
        return f"/activities/{self.ident}/edit"

    def payload(self):
        # assignment_time is mandatory (build_activity_payload rejects an empty one) and uses
        # the frontend datepicker's YYYY-MM-DDTHH:MM form, as the seed assignments do.
        return {"activity_name": self.tag, "activity_type": "Test",
                "activity_cost": "50.0", "duration": "2 hours",
                "assignment_time": "2026-11-01T09:00"}

    def update(self):
        # Activity has no description field; the name carries the marker instead.
        marker = f"updated-{self.tag}"
        data = {**self.payload(), "activity_name": marker}
        r = self.req("POST", self.update_path(), data=data)
        self.expect(r, (200, 201, 302, 303), "update")
        check = self.expect(self.req("GET", self.detail_path.format(id=self.ident)), (200,),
                            "read-after-update")
        if marker not in check.text:
            raise StepFailure(f"update did not persist: {marker!r} absent after the write")
        self.tag_after_update = marker
        return f"activity_name updated to {marker} and confirmed by a follow-up read"

    def delete_request(self):
        # Activity deletes over HTTP DELETE on the record itself, not a /delete form post.
        return self.req("DELETE", self.detail_path.format(id=self.ident))

    def delete(self):
        r = self.delete_request()
        self.expect(r, (200, 202, 204, 302, 303), "delete")
        check = self.req("GET", self.detail_path.format(id=self.ident))
        if check.status_code == 200 and getattr(self, "tag_after_update", self.tag) in check.text:
            raise StepFailure("delete returned success but the record is still readable")
        if check.status_code not in (404, 410, 302, 303):
            raise StepFailure(f"read-after-delete returned HTTP {check.status_code}, expected 404")
        return f"deleted and confirmed gone (read-after-delete HTTP {check.status_code})"


class Budget(Feature):
    """JSON in, HTML fragments out, through budget-fe's nginx /api/ proxy."""

    name = "budget-service"

    def create(self):
        body = {"trip_reference": "TRIP-1001", "expense": self.tag, "category": "CrudTest",
                "estimated_cost": 99.5, "destination_id": "dest-tokyo"}
        r = self.expect(self.req("POST", "/api/expenses", json=body), (200, 201), "create")
        ids = re.findall(r'data-expense-id="(\d+)"', r.text)
        if not ids:
            raise StepFailure("create response carried no data-expense-id to identify the new row")
        self.ident = int(ids[-1])
        return f"created expense {self.ident} via POST /api/expenses (HTTP {r.status_code})"

    def read(self):
        r = self.expect(self.req("GET", f"/api/expenses/{self.ident}"), (200,), "read")
        if self.tag not in r.text:
            raise StepFailure(f"read did not contain the tag {self.tag}")
        return f"GET /api/expenses/{self.ident} returned the tagged expense"

    def update(self):
        r = self.expect(self.req("PUT", f"/api/expenses/{self.ident}",
                                 json={"actual_cost": 42.0, "status": "Paid"}),
                        (200,), "update")
        check = self.expect(self.req("GET", f"/api/expenses/{self.ident}"), (200,),
                            "read-after-update")
        if "42" not in check.text or "Paid" not in check.text:
            raise StepFailure("update did not persist: actual_cost 42.0 / status Paid absent after the write")
        del r
        return "actual_cost set to 42.0 and status to Paid, confirmed by a follow-up read"

    def delete(self):
        r = self.expect(self.req("DELETE", f"/api/expenses/{self.ident}"),
                        (200, 202, 204), "delete")
        check = self.req("GET", f"/api/expenses/{self.ident}")
        if check.status_code == 200 and self.tag in check.text:
            raise StepFailure("delete returned success but the expense is still readable")
        if check.status_code not in (404, 410):
            raise StepFailure(f"read-after-delete returned HTTP {check.status_code}, expected 404")
        del r
        return "deleted and confirmed gone (read-after-delete HTTP 404)"


class Itinerary(Feature):
    """Pure JSON through itinerary-fe's nginx /api/ proxy."""

    name = "itinerary-service"

    def body(self, notes):
        return {"trip_reference": "TRIP-1001", "day": 9, "start_time": "06:00",
                "end_time": "07:00", "activity_id": 101, "destination_id": 1,
                "estimated_cost": 11.0, "notes": notes}

    def create(self):
        r = self.expect(self.req("POST", "/api/itinerary", json=self.body(f"created-{self.tag}")),
                        (200, 201), "create")
        try:
            self.ident = r.json()["itinerary_item_id"]
        except (ValueError, KeyError) as exc:
            raise StepFailure(f"create response carried no itinerary_item_id: {r.text[:120]}") from exc
        return f"created itinerary item {self.ident} via POST /api/itinerary (HTTP {r.status_code})"

    def read(self):
        r = self.expect(self.req("GET", f"/api/itinerary/{self.ident}"), (200,), "read")
        if f"created-{self.tag}" not in r.text:
            raise StepFailure(f"read did not contain the tag created-{self.tag}")
        return f"GET /api/itinerary/{self.ident} returned the tagged item"

    def update(self):
        marker = f"updated-{self.tag}"
        self.expect(self.req("PUT", f"/api/itinerary/{self.ident}", json=self.body(marker)),
                    (200,), "update")
        check = self.expect(self.req("GET", f"/api/itinerary/{self.ident}"), (200,),
                            "read-after-update")
        if marker not in check.text:
            raise StepFailure(f"update did not persist: {marker!r} absent after the write")
        return f"notes updated to {marker} and confirmed by a follow-up read"

    def delete(self):
        self.expect(self.req("DELETE", f"/api/itinerary/{self.ident}"), (200, 202, 204), "delete")
        check = self.req("GET", f"/api/itinerary/{self.ident}")
        if check.status_code == 200 and self.tag in check.text:
            raise StepFailure("delete returned success but the item is still readable")
        if check.status_code not in (404, 410):
            raise StepFailure(f"read-after-delete returned HTTP {check.status_code}, expected 404")
        return "deleted and confirmed gone (read-after-delete HTTP 404)"


FEATURES = (Destination, Accommodation, Activity, Budget, Itinerary)


def run_feature(cls, base, tag, log):
    feature = cls(base, tag, log)
    entry = {"tag": tag, "notes": ""}
    notes = []
    log(f"\n--- {cls.name}")
    for step in STEPS:
        if any(entry.get(s) == "FAIL" for s in STEPS):
            # A lifecycle is a chain: once a step fails the rest cannot be evidenced.
            entry[step] = "FAIL"
            notes.append(f"{step}: not attempted (an earlier step failed)")
            log(f"    {step:7} SKIPPED (earlier failure)")
            continue
        try:
            note = getattr(feature, step)()
        except StepFailure as exc:
            entry[step] = "FAIL"
            notes.append(f"{step}: {exc}")
            log(f"    {step:7} FAIL  {exc}")
        else:
            entry[step] = "PASS"
            notes.append(f"{step}: {note}")
            log(f"    {step:7} PASS  {note}")
    entry["notes"] = "; ".join(notes)
    return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--alb-url", required=True,
                        help="http://<alb dns name>, exactly as recorded in stack-outputs-<run>.json")
    parser.add_argument("--verified-by", default="unspecified",
                        help="who is accountable for this verification")
    parser.add_argument("--out-dir", default=str(CLOUD_DIR),
                        help="where to write crud-lifecycle-evidence-<UTC>.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and exit without touching the deployment")
    args = parser.parse_args(argv)

    base = args.alb_url.rstrip("/")
    tag = f"r2-crud-{utc_stamp()}"

    print("=== VoyageAI CRUD lifecycle verification ===")
    print(f"ALB: {base}")
    print(f"tag: {tag}")
    if args.dry_run:
        print("mode: DRY RUN - nothing will be created, changed or deleted.")
        for cls in FEATURES:
            print(f"  would run create/read/update/delete on {cls.name} (port {PORTS[cls.name]})")
        return 0

    features = {}
    for cls in FEATURES:
        features[cls.name] = run_feature(cls, base, tag, print)

    doc = {"format": LIFECYCLE_FORMAT, "verified_at": utc_iso(), "verified_by": args.verified_by,
           "alb_url": base, "features": features}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"crud-lifecycle-evidence-{utc_stamp()}.json"
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    failed = sorted(n for n, e in features.items() if any(e[s] == "FAIL" for s in STEPS))
    print(f"\n=== Evidence written: {out} ===")
    if failed:
        print(f"=== {len(failed)} feature(s) FAILED their lifecycle: {', '.join(failed)} ===",
              file=sys.stderr)
        return 1
    print(f"=== All {len(features)} features completed a tagged create/read/update/delete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
