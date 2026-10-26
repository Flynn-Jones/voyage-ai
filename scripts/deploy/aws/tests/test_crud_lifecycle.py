"""Offline tests for scripts/deploy/aws/crud-lifecycle.py.

`requests.request` is replaced by a fake router backed by an in-memory store, so no HTTP
leaves the process. The point of these tests is the script's HONESTY: it writes release
evidence, so the failure that matters is recording PASS for a step that did not happen.

Run from the repo root: .venv/bin/python -m pytest scripts/deploy/aws/tests
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "crud-lifecycle.py"
ALB = "http://alb.test"
STEPS = ("create", "read", "update", "delete")


def load():
    """Import the hyphenated script as a module."""
    spec = importlib.util.spec_from_file_location("crud_lifecycle", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crud = load()


class Response:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text if text or payload is None else json.dumps(payload)
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeFleet:
    """A deployment that behaves correctly, with switches to make one step misbehave.

    `broken` maps "<feature>.<step>" to a fault:
      "status"      - the write answers 4xx
      "no_persist"  - the write answers 2xx but the record never changes
      "undead"      - the delete answers 2xx but the record is still readable
    """

    def __init__(self, broken=None):
        self.broken = broken or {}
        self.records = {}      # (port, id) -> text blob
        self.next_id = {3001: 11, 3002: 22, 3003: 16, 3004: 13, 3005: 14}
        self.calls = []

    # -- routing ---------------------------------------------------------
    def __call__(self, method, url, **kw):
        self.calls.append(f"{method} {url}")
        m = re.match(r"http://alb\.test:(\d+)(/.*)$", url)
        assert m, url
        port, path = int(m.group(1)), m.group(2)
        body = kw.get("data") or kw.get("json") or {}
        return getattr(self, f"_p{port}")(method, path, body)

    def fault(self, feature, step):
        return self.broken.get(f"{feature}.{step}")

    def _create(self, port, feature, text, redirect=302):
        if self.fault(feature, "create") == "status":
            return Response(400, "validation failed")
        ident = self.next_id[port]
        self.next_id[port] += 1
        self.records[(port, ident)] = text
        return Response(redirect, "")

    def _write(self, port, feature, ident, text):
        if self.fault(feature, "update") == "status":
            return Response(400, "validation failed")
        if (port, ident) not in self.records:
            return Response(404, "")
        if self.fault(feature, "update") != "no_persist":
            self.records[(port, ident)] = text
        return Response(302, "")

    def _delete(self, port, feature, ident, code=302):
        if self.fault(feature, "delete") == "status":
            return Response(400, "")
        if self.fault(feature, "delete") != "undead":
            self.records.pop((port, ident), None)
        return Response(code, "")

    def _detail(self, port, ident):
        text = self.records.get((port, ident))
        return Response(200, text) if text is not None else Response(404, "not found")

    # -- per-feature endpoints -------------------------------------------
    def _p3001(self, method, path, body):
        if path == "/":
            return Response(200, "".join(
                f'<a href="/destinations/{i}"></a>' for (p, i) in self.records if p == 3001))
        if path == "/destinations/new":
            return self._create(3001, "destination-service", body.get("description", ""))
        if m := re.fullmatch(r"/destinations/(\d+)/edit", path):
            return self._write(3001, "destination-service", int(m.group(1)), body.get("description", ""))
        if m := re.fullmatch(r"/destinations/(\d+)/delete", path):
            return self._delete(3001, "destination-service", int(m.group(1)))
        if m := re.fullmatch(r"/destinations/(\d+)", path):
            return self._detail(3001, int(m.group(1)))
        raise AssertionError(path)

    def _p3002(self, method, path, body):
        if path == "/":
            return Response(200, "".join(
                f'<a href="/accommodations/{i}"></a>' for (p, i) in self.records if p == 3002))
        if path == "/accommodations":
            return self._create(3002, "accommodation-service", body.get("description", ""))
        if m := re.fullmatch(r"/accommodations/(\d+)/delete", path):
            return self._delete(3002, "accommodation-service", int(m.group(1)))
        if m := re.fullmatch(r"/accommodations/(\d+)", path):
            ident = int(m.group(1))
            if method == "POST":
                return self._write(3002, "accommodation-service", ident, body.get("description", ""))
            return self._detail(3002, ident)
        raise AssertionError(path)

    def _p3003(self, method, path, body):
        if path == "/":
            return Response(200, "".join(
                f'<tr id="activity-row-{i}">' for (p, i) in self.records if p == 3003))
        if path == "/activities/register":
            if not body.get("assignment_time"):
                return Response(400, "Please select a recommended/assigned time.")
            return self._create(3003, "activity-service", body.get("activity_name", ""))
        if m := re.fullmatch(r"/activities/(\d+)/edit", path):
            return self._write(3003, "activity-service", int(m.group(1)), body.get("activity_name", ""))
        if m := re.fullmatch(r"/activities/(\d+)", path):
            ident = int(m.group(1))
            if method == "DELETE":
                return self._delete(3003, "activity-service", ident, code=200)
            return self._detail(3003, ident)
        raise AssertionError(path)

    def _p3004(self, method, path, body):
        if path == "/api/expenses":
            if self.fault("budget-service", "create") == "status":
                return Response(400, "")
            ident = self.next_id[3004]
            self.next_id[3004] += 1
            self.records[(3004, ident)] = f'<tr data-expense-id="{ident}">{body.get("expense", "")}</tr>'
            return Response(201, self.records[(3004, ident)])
        if m := re.fullmatch(r"/api/expenses/(\d+)", path):
            ident = int(m.group(1))
            if method == "PUT":
                if self.fault("budget-service", "update") == "status":
                    return Response(400, "")
                if (3004, ident) not in self.records:
                    return Response(404, "")
                if self.fault("budget-service", "update") != "no_persist":
                    self.records[(3004, ident)] += " 42 Paid"
                return Response(200, self.records[(3004, ident)])
            if method == "DELETE":
                if self.fault("budget-service", "delete") != "undead":
                    self.records.pop((3004, ident), None)
                return Response(204, "")
            return self._detail(3004, ident)
        raise AssertionError(path)

    def _p3005(self, method, path, body):
        if path == "/api/itinerary":
            if self.fault("itinerary-service", "create") == "status":
                return Response(400, "")
            ident = self.next_id[3005]
            self.next_id[3005] += 1
            self.records[(3005, ident)] = body.get("notes", "")
            return Response(201, payload={"itinerary_item_id": ident})
        if m := re.fullmatch(r"/api/itinerary/(\d+)", path):
            ident = int(m.group(1))
            if method == "PUT":
                if self.fault("itinerary-service", "update") == "status":
                    return Response(400, "")
                if (3005, ident) not in self.records:
                    return Response(404, "")
                if self.fault("itinerary-service", "update") != "no_persist":
                    self.records[(3005, ident)] = body.get("notes", "")
                return Response(200, self.records[(3005, ident)])
            if method == "DELETE":
                if self.fault("itinerary-service", "delete") != "undead":
                    self.records.pop((3005, ident), None)
                return Response(204, "")
            text = self.records.get((3005, ident))
            if text is None:
                return Response(404, "")
            return Response(200, payload={"itinerary_item_id": ident, "notes": text})
        raise AssertionError(path)


@pytest.fixture
def fleet(monkeypatch):
    def install(broken=None):
        f = FakeFleet(broken)
        monkeypatch.setattr(crud.requests, "request", f)
        return f
    return install


def run(tmp_path, fleet_obj=None, extra=()):
    code = crud.main(["--alb-url", ALB, "--verified-by", "tester",
                      "--out-dir", str(tmp_path), *extra])
    files = list(tmp_path.glob("crud-lifecycle-evidence-*.json"))
    doc = json.loads(files[0].read_text()) if files else None
    del fleet_obj
    return code, doc, files


# ---- the happy path ----------------------------------------------------

def test_all_five_features_pass_and_the_evidence_binds_to_the_deployment(tmp_path, fleet):
    f = fleet()
    code, doc, files = run(tmp_path, f)

    assert code == 0
    assert len(files) == 1
    assert doc["format"] == "voyageai-crud-lifecycle/1"
    assert doc["alb_url"] == ALB and doc["verified_by"] == "tester"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", doc["verified_at"])
    assert set(doc["features"]) == set(crud.PORTS)
    for name, entry in doc["features"].items():
        assert [entry[s] for s in STEPS] == ["PASS"] * 4, name
        assert entry["tag"].startswith("r2-crud-")


def test_a_successful_run_leaves_nothing_behind(tmp_path, fleet):
    f = fleet()
    code, _, _ = run(tmp_path, f)

    assert code == 0
    assert f.records == {}, "every created record must be deleted again"


def test_activity_create_sends_the_mandatory_assignment_time(tmp_path, fleet):
    # The real frontend 400s without it; that bug cost a whole run.
    f = fleet()
    run(tmp_path, f)

    assert crud.Activity(ALB, "t", lambda _: None).payload()["assignment_time"]
    assert not any("400" in c for c in f.calls)


# ---- honesty: a misbehaving step is never recorded as PASS -------------

@pytest.mark.parametrize("feature", sorted(crud.PORTS))
def test_a_failed_create_fails_the_feature_and_skips_the_rest(tmp_path, fleet, feature):
    f = fleet({f"{feature}.create": "status"})
    code, doc, _ = run(tmp_path, f)

    assert code == 1
    entry = doc["features"][feature]
    assert [entry[s] for s in STEPS] == ["FAIL"] * 4
    assert "not attempted" in entry["notes"]
    # the other four are untouched by one feature's failure
    others = [n for n in crud.PORTS if n != feature]
    assert all(doc["features"][o][s] == "PASS" for o in others for s in STEPS)


@pytest.mark.parametrize("feature", sorted(crud.PORTS))
def test_an_update_that_does_not_persist_is_a_failure(tmp_path, fleet, feature):
    f = fleet({f"{feature}.update": "no_persist"})
    code, doc, _ = run(tmp_path, f)

    assert code == 1
    entry = doc["features"][feature]
    assert entry["create"] == "PASS" and entry["read"] == "PASS"
    assert entry["update"] == "FAIL" and "did not persist" in entry["notes"]


@pytest.mark.parametrize("feature", sorted(crud.PORTS))
def test_a_delete_that_leaves_the_record_readable_is_a_failure(tmp_path, fleet, feature):
    # The critical one: a 2xx from DELETE is not evidence the row is gone.
    f = fleet({f"{feature}.delete": "undead"})
    code, doc, _ = run(tmp_path, f)

    assert code == 1
    entry = doc["features"][feature]
    assert [entry[s] for s in STEPS[:3]] == ["PASS"] * 3
    assert entry["delete"] == "FAIL"
    assert "still readable" in entry["notes"] or "expected 404" in entry["notes"]


def test_a_write_rejected_with_4xx_is_a_failure(tmp_path, fleet):
    f = fleet({"itinerary-service.update": "status"})
    code, doc, _ = run(tmp_path, f)

    assert code == 1
    assert doc["features"]["itinerary-service"]["update"] == "FAIL"


def test_every_failure_is_reported_in_the_exit_code(tmp_path, fleet):
    f = fleet({"budget-service.create": "status", "activity-service.delete": "undead"})
    code, doc, _ = run(tmp_path, f)

    assert code == 1
    failed = {n for n, e in doc["features"].items() if any(e[s] == "FAIL" for s in STEPS)}
    assert failed == {"budget-service", "activity-service"}


# ---- dry run -----------------------------------------------------------

def test_dry_run_touches_nothing_and_writes_no_evidence(tmp_path, fleet):
    f = fleet()
    code, doc, files = run(tmp_path, f, extra=("--dry-run",))

    assert code == 0
    assert files == [] and doc is None
    assert f.calls == [], "a dry run must not issue a single request"
    assert f.records == {}
