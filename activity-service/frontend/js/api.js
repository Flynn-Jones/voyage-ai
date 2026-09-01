// Activity Manager backend base URL (Backend/API 3, external port 5003).
// Update this in one place if the backend's host/port changes.
const API_BASE_URL = "http://localhost:5003/api";

async function getActivities() {
  const response = await fetch(`${API_BASE_URL}/view_activities`);
  if (!response.ok) {
    throw new Error(`Failed to load activities (${response.status})`);
  }
  return response.json();
}

async function getActivity(id) {
  const response = await fetch(`${API_BASE_URL}/view_activity/${encodeURIComponent(id)}`);
  if (!response.ok) {
    throw new Error(`Failed to load activity ${id} (${response.status})`);
  }
  return response.json();
}

async function addActivity(payload) {
  const response = await fetch(`${API_BASE_URL}/add_activity`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody.error || `Failed to add activity (${response.status})`);
  }
  return response.json();
}

// ASSUMPTION (flagged, unconfirmed): no AI-summary route was given in the spec.
// Mirrors the ai-compare pattern used by other features. Correct the path/method
// here if the real backend differs.
async function getActivitySummary(activityId) {
  const response = await fetch(`${API_BASE_URL}/activity/ai-summary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ activity_id: activityId }),
  });
  if (!response.ok) {
    throw new Error(`Failed to generate AI summary (${response.status})`);
  }
  return response.json();
}