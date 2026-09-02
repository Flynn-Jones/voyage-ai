"""Live-stack tests for the destination frontend (port 3001).

Requires the stack to be running:
    docker compose -f student-1/docker-compose.yml up -d --build
    pytest student-1/tests/test_frontend_live.py -v

Exercises the browser-facing behaviour end to end: the seeded list, the
frontend-side search and country filter, the HTMX partial response, the
static theme asset, and a full create -> edit -> delete lifecycle driven
through the frontend's own routes (never a hard-coded destination id).
"""
import re

import pytest
import requests

pytestmark = pytest.mark.live

FRONTEND_URL = "http://localhost:3001"
BACKEND_URL = "http://localhost:5001"

SEEDED_CITIES = [
    "Tokyo", "Kyoto", "Osaka", "Seoul", "Bangkok",
    "Singapore", "Sydney", "Melbourne", "Paris", "Rome",
]


def test_index_lists_at_least_ten_seeded_destinations():
    response = requests.get(f"{FRONTEND_URL}/", timeout=5)
    assert response.status_code == 200
    for city in SEEDED_CITIES:
        assert city in response.text


def test_search_narrows_results():
    response = requests.get(f"{FRONTEND_URL}/", params={"q": "Tokyo"}, timeout=5)
    assert response.status_code == 200
    assert "Tokyo" in response.text
    assert "Paris" not in response.text


def test_country_filter_narrows_results():
    response = requests.get(f"{FRONTEND_URL}/", params={"country": "Japan"}, timeout=5)
    assert response.status_code == 200
    assert "Kyoto" in response.text
    assert "Paris" not in response.text


def test_no_match_search_shows_empty_state_not_error():
    response = requests.get(f"{FRONTEND_URL}/", params={"q": "zzzznotarealplace"}, timeout=5)
    assert response.status_code == 200
    assert "No destinations match your search" in response.text
    assert "alert--error" not in response.text


def test_hx_request_returns_partial_fragment():
    response = requests.get(
        f"{FRONTEND_URL}/", params={"q": "Tokyo"}, headers={"HX-Request": "true"}, timeout=5
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Tokyo" in response.text


def test_static_theme_css_is_served():
    response = requests.get(f"{FRONTEND_URL}/static/theme.css", timeout=5)
    assert response.status_code == 200
    assert "voyage-primary" in response.text


def test_full_crud_lifecycle_through_frontend():
    created_id = None
    try:
        create_response = requests.post(
            f"{FRONTEND_URL}/destinations/new",
            data={
                "city": "Demo City",
                "country": "Australia",
                "description": "Temporary UI test record",
                "average_daily_cost": "150",
                "recommended_trip_length": "3",
                "travel_style": "City break",
                "categories": "food, testing",
            },
            timeout=5,
        )
        assert create_response.status_code == 200  # after following the redirect
        match = re.search(r"/destinations/(\d+)$", create_response.url)
        assert match, f"unexpected redirect target: {create_response.url}"
        created_id = int(match.group(1))
        assert "Demo City" in create_response.text

        list_response = requests.get(f"{FRONTEND_URL}/", timeout=5)
        assert "Demo City" in list_response.text

        edit_response = requests.post(
            f"{FRONTEND_URL}/destinations/{created_id}/edit",
            data={"city": "Demo City", "country": "Australia", "average_daily_cost": "275"},
            timeout=5,
        )
        assert edit_response.status_code == 200
        assert "275.00" in edit_response.text

        detail_response = requests.get(f"{FRONTEND_URL}/destinations/{created_id}", timeout=5)
        assert "275.00" in detail_response.text

        confirm_response = requests.get(f"{FRONTEND_URL}/destinations/{created_id}/delete", timeout=5)
        assert confirm_response.status_code == 200
        assert "Delete this destination" in confirm_response.text

        delete_response = requests.post(f"{FRONTEND_URL}/destinations/{created_id}/delete", timeout=5)
        assert delete_response.status_code == 200
        created_id = None  # deleted successfully; nothing left to clean up

        final_list = requests.get(f"{FRONTEND_URL}/", timeout=5)
        assert "Demo City" not in final_list.text
    finally:
        if created_id is not None:
            requests.delete(f"{BACKEND_URL}/api/destinations/{created_id}", timeout=5)
