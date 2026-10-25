"""Real temporary SQLite transactions: scope, stale snapshots, rollback and durable replay."""
import copy
import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("app", "database", "init_db", "validation"):
    sys.modules.pop(name, None)
importlib.invalidate_caches()
from app import create_app  # noqa: E402 - isolate the database tier from backend module names
from database import get_connection  # noqa: E402 - same database module isolation


@pytest.fixture
def setup(tmp_path):
    path = str(tmp_path / "application.sqlite")
    app = create_app(path)
    client = app.test_client()
    base = {"trip_reference": "ATOMIC-TRIP", "day": 1, "activity_id": 1, "destination_id": 1, "estimated_cost": 10, "notes": None}
    rows = []
    for start, end in [("09:00", "11:00"), ("10:00", "11:00"), ("10:30", "11:30")]:
        response = client.post("/itinerary-items", json={**base, "start_time": start, "end_time": end})
        assert response.status_code == 201
        rows.append(response.json)
    changes = [{**r, "id": str(r["itinerary_item_id"]), "original": dict(r), "reason": "Remove overlap",
                "start_time": f"{11 + n}:00", "end_time": f"{12 + n}:00"} for n, r in enumerate(rows[1:])]
    payload = {"workflow": "itinerary_schedule_review", "trip_reference": "ATOMIC-TRIP", "day": 1,
               "decision": "approve", "changes": changes}
    return app, client, rows, payload, path


def apply(client, payload):
    return client.post("/multi-agent/applications/run-db-test", json=payload)


def test_atomic_apply_and_durable_retry_after_restart(setup):
    _, client, rows, payload, path = setup
    result = apply(client, payload)
    ids = [r["itinerary_item_id"] for r in rows[1:]]
    assert result.status_code == 200 and result.json["updated_item_ids"] == ids
    assert result.json["replayed"] is False
    restarted = create_app(path).test_client()
    replay = apply(restarted, payload)
    assert replay.status_code == 200 and replay.json["replayed"] is True
    for change in payload["changes"]:
        assert restarted.get(f"/itinerary-items/{change['itinerary_item_id']}").json["start_time"] == change["start_time"]
    connection = get_connection(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM itinerary_multi_agent_applications").fetchone()[0] == 1
    finally:
        connection.close()


def test_concurrent_apply_only_writes_once(setup):
    app, _, _, payload, _ = setup

    def call(_):
        with app.test_client() as client:
            result = apply(client, payload)
            return result.status_code, result.json
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(call, range(2)))
    assert [r[0] for r in results] == [200, 200]
    assert sorted(r[1]["replayed"] for r in results) == [False, True]


def test_different_replay_payload_refused(setup):
    _, client, _, payload, _ = setup
    assert apply(client, payload).status_code == 200
    payload["changes"][0]["notes"] = "Not the first accepted representation"
    assert apply(client, payload).status_code == 409


def test_second_stale_record_rolls_back_every_change(setup):
    _, client, rows, payload, path = setup
    stale = rows[2]
    assert client.put(f"/itinerary-items/{stale['itinerary_item_id']}", json={**stale, "notes": "Edited after review"}).status_code == 200
    assert apply(client, payload).status_code == 409
    assert client.get(f"/itinerary-items/{rows[1]['itinerary_item_id']}").json == rows[1]
    connection = get_connection(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM itinerary_multi_agent_applications").fetchone()[0] == 0
    finally:
        connection.close()


def test_partial_accept_cannot_introduce_a_new_clash(setup):
    _, client, rows, payload, _ = setup
    client.post("/itinerary-items", json={**rows[0], "start_time": "11:45", "end_time": "12:00"})
    payload["decision"] = "partial"
    payload["changes"] = payload["changes"][:1]
    result = apply(client, payload)
    assert result.status_code == 409 and "overlap" in result.json["error"]
    assert client.get(f"/itinerary-items/{rows[1]['itinerary_item_id']}").json == rows[1]


@pytest.mark.parametrize("mutation", ["rejected", "workflow", "trip", "day", "duration", "cost", "id", "duplicate", "nan"])
def test_invalid_approval_never_writes(setup, mutation):
    _, client, rows, original, _ = setup
    payload = copy.deepcopy(original)
    if mutation == "rejected":
        payload["decision"] = "reject"
    elif mutation == "workflow":
        payload["workflow"] = "budget_reconcile"
    elif mutation == "trip":
        payload["changes"][0]["trip_reference"] = "OTHER"
    elif mutation == "day":
        payload["changes"][0]["day"] = 2
    elif mutation == "duration":
        payload["changes"][0]["end_time"] = "13:00"
    elif mutation == "cost":
        payload["changes"][0]["estimated_cost"] = 12
    elif mutation == "id":
        payload["changes"][0]["itinerary_item_id"] = 999
    elif mutation == "duplicate":
        payload["changes"].append(payload["changes"][0])
    else:
        payload["changes"][0]["estimated_cost"] = float("nan")
    assert apply(client, payload).status_code == 400
    for row in rows:
        assert client.get(f"/itinerary-items/{row['itinerary_item_id']}").json == row


def test_corrected_notes_and_other_trip_untouched(setup):
    _, client, rows, payload, _ = setup
    foreign = client.post("/itinerary-items", json={**rows[0], "trip_reference": "OTHER"}).json
    payload["decision"] = "correct"
    payload["changes"][0]["notes"] = "Human confirmation"
    assert apply(client, payload).status_code == 200
    assert client.get(f"/itinerary-items/{foreign['itinerary_item_id']}").json == foreign
    assert client.get(f"/itinerary-items/{rows[1]['itinerary_item_id']}").json["notes"] == "Human confirmation"


def test_missing_deleted_record_does_not_recreate_it(setup):
    _, client, rows, payload, _ = setup
    assert client.delete(f"/itinerary-items/{rows[2]['itinerary_item_id']}").status_code == 204
    assert apply(client, payload).status_code == 409
    assert client.get(f"/itinerary-items/{rows[2]['itinerary_item_id']}").status_code == 404
