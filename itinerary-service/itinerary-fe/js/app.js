"use strict";

const API_URL = "/api/itinerary";
const elements = {};
let availableDays = [];

async function apiRequest(path = "", options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  if (response.status === 204) return null;

  let body;
  try {
    body = await response.json();
  } catch (_) {
    throw new Error("The server returned an unreadable response.");
  }
  if (!response.ok) throw new Error(body.error || `Request failed with status ${response.status}.`);
  return body;
}

const itineraryApi = {
  list: () => apiRequest(),
  listDay: (day) => apiRequest(`/day/${day}`),
  create: (item) => apiRequest("", { method: "POST", body: JSON.stringify(item) }),
  update: (id, item) => apiRequest(`/${id}`, { method: "PUT", body: JSON.stringify(item) }),
  remove: (id) => apiRequest(`/${id}`, { method: "DELETE" }),
  review: (request) => apiRequest("/ai-review", { method: "POST", body: JSON.stringify(request) }),
};

function showMessage(text, type) {
  elements.message.textContent = text;
  elements.message.className = `message message--${type}`;
  elements.message.hidden = false;
}

function clearMessage() {
  elements.message.hidden = true;
  elements.message.textContent = "";
}

function setLoading(isLoading) {
  elements.loading.hidden = !isLoading;
  elements.list.hidden = isLoading;
  elements.dayFilter.disabled = isLoading;
  if (isLoading) elements.empty.hidden = true;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatCost(value) {
  return new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" }).format(value);
}

function createItemCard(item) {
  const card = makeElement("article", "itinerary-card");
  const time = makeElement("div", "item-time", `${item.start_time}–${item.end_time}`);
  const details = makeElement("div", "item-details");
  const meta = makeElement("div", "item-meta");
  meta.append(
    makeElement("span", "tag", `Destination ${item.destination_id}`),
    makeElement("span", "tag", `Activity ${item.activity_id}`),
    makeElement("span", "tag item-cost", formatCost(item.estimated_cost)),
  );
  details.append(meta, makeElement("p", "item-notes", item.notes || "No notes added."));

  const actions = makeElement("div", "item-actions");
  const editButton = makeElement("button", "button button--secondary button--small", "Edit");
  editButton.type = "button";
  editButton.addEventListener("click", () => beginEdit(item));
  const deleteButton = makeElement("button", "button button--danger button--small", "Delete");
  deleteButton.type = "button";
  deleteButton.addEventListener("click", () => deleteItem(item));
  actions.append(editButton, deleteButton);
  card.append(time, details, actions);
  return card;
}

function renderItems(items) {
  elements.list.replaceChildren();
  elements.list.hidden = false;
  elements.empty.hidden = items.length !== 0;
  if (!items.length) return;

  const grouped = new Map();
  items.forEach((item) => {
    if (!grouped.has(item.day)) grouped.set(item.day, []);
    grouped.get(item.day).push(item);
  });

  [...grouped.entries()]
    .sort(([firstDay], [secondDay]) => firstDay - secondDay)
    .forEach(([day, dayItems]) => {
      const section = makeElement("section", "day-group");
      section.append(makeElement("h3", "day-heading", `Day ${day}`));
      const timeline = makeElement("div", "timeline");
      dayItems
        .sort((first, second) => first.start_time.localeCompare(second.start_time))
        .forEach((item) => timeline.append(createItemCard(item)));
      section.append(timeline);
      elements.list.append(section);
    });
}

function updateDayButtons() {
  const selected = elements.dayFilter.value;
  const position = selected === "all" ? -1 : availableDays.indexOf(Number(selected));
  elements.previousDay.disabled = position <= 0;
  elements.nextDay.disabled = availableDays.length === 0 || position >= availableDays.length - 1;
}

function updateDayOptions(allItems) {
  const selected = elements.dayFilter.value;
  availableDays = [...new Set(allItems.map((item) => item.day))].sort((a, b) => a - b);
  const options = [new Option("All days", "all")];
  availableDays.forEach((day) => options.push(new Option(`Day ${day}`, String(day))));
  elements.dayFilter.replaceChildren(...options);
  elements.dayFilter.value = availableDays.includes(Number(selected)) ? selected : "all";
  updateDayButtons();
}

async function loadItinerary({ refreshDays = true } = {}) {
  setLoading(true);
  try {
    if (refreshDays) {
      const allItems = await itineraryApi.list();
      updateDayOptions(allItems);
      renderItems(elements.dayFilter.value === "all" ? allItems : await itineraryApi.listDay(elements.dayFilter.value));
    } else {
      renderItems(elements.dayFilter.value === "all" ? await itineraryApi.list() : await itineraryApi.listDay(elements.dayFilter.value));
    }
  } catch (error) {
    elements.list.replaceChildren();
    showMessage(error.message, "error");
  } finally {
    setLoading(false);
  }
}

function openCreateForm() {
  elements.form.reset();
  elements.itemId.value = "";
  elements.formMode.textContent = "New schedule entry";
  elements.formTitle.textContent = "Add itinerary item";
  elements.submitButton.textContent = "Add item";
  elements.formError.hidden = true;
  elements.formPanel.hidden = false;
  elements.tripReference.focus();
}

function beginEdit(item) {
  elements.itemId.value = item.itinerary_item_id;
  elements.tripReference.value = item.trip_reference;
  elements.day.value = item.day;
  elements.startTime.value = item.start_time;
  elements.endTime.value = item.end_time;
  elements.destinationId.value = item.destination_id;
  elements.activityId.value = item.activity_id;
  elements.estimatedCost.value = item.estimated_cost;
  elements.notes.value = item.notes || "";
  elements.formMode.textContent = `Editing item ${item.itinerary_item_id}`;
  elements.formTitle.textContent = "Edit itinerary item";
  elements.submitButton.textContent = "Save changes";
  elements.formError.hidden = true;
  elements.formPanel.hidden = false;
  elements.formPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeForm() {
  elements.form.reset();
  elements.itemId.value = "";
  elements.formError.hidden = true;
  elements.formPanel.hidden = true;
}

function formPayload() {
  return {
    trip_reference: elements.tripReference.value.trim(),
    day: Number(elements.day.value),
    start_time: elements.startTime.value,
    end_time: elements.endTime.value,
    activity_id: Number(elements.activityId.value),
    destination_id: Number(elements.destinationId.value),
    estimated_cost: Number(elements.estimatedCost.value),
    notes: elements.notes.value,
  };
}

function validatePayload(payload) {
  if (!elements.form.reportValidity()) return "Please complete all required fields correctly.";
  if (payload.end_time <= payload.start_time) return "End time must be later than start time.";
  return null;
}

async function submitForm(event) {
  event.preventDefault();
  clearMessage();
  const payload = formPayload();
  const validationError = validatePayload(payload);
  if (validationError) {
    elements.formError.textContent = validationError;
    elements.formError.hidden = false;
    return;
  }

  const itemId = elements.itemId.value;
  elements.submitButton.disabled = true;
  elements.submitButton.textContent = itemId ? "Saving…" : "Adding…";
  try {
    if (itemId) {
      await itineraryApi.update(itemId, payload);
      showMessage("Itinerary item updated.", "success");
    } else {
      await itineraryApi.create(payload);
      showMessage("Itinerary item added.", "success");
    }
    closeForm();
    await loadItinerary();
  } catch (error) {
    elements.formError.textContent = error.message;
    elements.formError.hidden = false;
  } finally {
    elements.submitButton.disabled = false;
    elements.submitButton.textContent = itemId ? "Save changes" : "Add item";
  }
}

async function deleteItem(item) {
  if (!window.confirm(`Delete the ${item.start_time} itinerary item on Day ${item.day}?`)) return;
  clearMessage();
  try {
    await itineraryApi.remove(item.itinerary_item_id);
    showMessage("Itinerary item deleted.", "success");
    await loadItinerary();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function changeDay() {
  updateDayButtons();
  clearMessage();
  await loadItinerary({ refreshDays: false });
}

function moveDay(offset) {
  const position = availableDays.indexOf(Number(elements.dayFilter.value));
  const nextPosition = position + offset;
  if (nextPosition >= 0 && nextPosition < availableDays.length) {
    elements.dayFilter.value = String(availableDays[nextPosition]);
    changeDay();
  }
}

function workflowStage(name, content) {
  const stage = makeElement("section", `workflow-stage workflow-stage--${name.toLowerCase()}`);
  stage.append(makeElement("h3", "workflow-stage__title", name), content);
  return stage;
}

function renderReview(review) {
  const plan = makeElement("div", "workflow-content");
  plan.append(makeElement("p", "", `Day ${review.plan.requested_day}`));
  const checks = makeElement("ul", "workflow-list");
  review.plan.checks.forEach((check) => checks.append(makeElement("li", "", check)));
  plan.append(checks);
  const act = makeElement("div", "workflow-content");
  act.append(makeElement("p", "", `${review.act.records_retrieved} record(s) retrieved via the database API.`));
  const observe = makeElement("div", "workflow-content");
  const facts = [`${review.observe.item_count} itinerary item(s)`, `${review.observe.total_scheduled_minutes} scheduled minutes`, `${review.observe.overlaps.length} overlap(s)`, `${review.observe.short_gaps.length} short gap(s)`, `${formatCost(review.observe.total_estimated_cost)} estimated cost`];
  const factList = makeElement("ul", "workflow-list");
  facts.forEach((fact) => factList.append(makeElement("li", "", fact)));
  observe.append(factList);
  const adapt = makeElement("div", "workflow-content");
  adapt.append(makeElement("p", "ai-recommendation", review.adapt.recommendation), makeElement("p", "ai-disclaimer", `Advisory only — generated locally with ${review.adapt.model}.`));
  elements.aiReviewResults.replaceChildren(workflowStage("Plan", plan), workflowStage("Act", act), workflowStage("Observe", observe), workflowStage("Adapt", adapt));
  elements.aiReviewResults.hidden = false;
}

async function submitAiReview(event) {
  event.preventDefault();
  elements.aiReviewError.hidden = true;
  elements.aiReviewResults.hidden = true;
  elements.aiReviewLoading.hidden = false;
  elements.aiReviewButton.disabled = true;
  try {
    renderReview(await itineraryApi.review({day: Number(elements.aiReviewDay.value), prompt: elements.aiReviewPrompt.value.trim()}));
  } catch (error) {
    elements.aiReviewError.textContent = error.message;
    elements.aiReviewError.hidden = false;
  } finally {
    elements.aiReviewLoading.hidden = true;
    elements.aiReviewButton.disabled = false;
  }
}

function cacheElements() {
  const ids = ["message", "form-panel", "form-mode", "form-title", "itinerary-form", "item-id", "trip-reference", "day", "start-time", "end-time", "destination-id", "activity-id", "estimated-cost", "notes", "form-error", "submit-button", "day-filter", "previous-day", "next-day", "loading", "itinerary-list", "empty-state", "ai-review-form", "ai-review-day", "ai-review-prompt", "ai-review-button", "ai-review-loading", "ai-review-error", "ai-review-results"];
  ids.forEach((id) => {
    const property = id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    elements[property] = document.getElementById(id);
  });
  elements.form = elements.itineraryForm;
  elements.list = elements.itineraryList;
  elements.empty = elements.emptyState;
  elements.openCreateButton = document.getElementById("open-create-button");
  elements.closeFormButton = document.getElementById("close-form-button");
  elements.cancelEditButton = document.getElementById("cancel-edit-button");
}

document.addEventListener("DOMContentLoaded", () => {
  cacheElements();
  elements.openCreateButton.addEventListener("click", openCreateForm);
  elements.closeFormButton.addEventListener("click", closeForm);
  elements.cancelEditButton.addEventListener("click", closeForm);
  elements.form.addEventListener("submit", submitForm);
  elements.dayFilter.addEventListener("change", changeDay);
  elements.previousDay.addEventListener("click", () => moveDay(-1));
  elements.nextDay.addEventListener("click", () => moveDay(1));
  elements.aiReviewForm.addEventListener("submit", submitAiReview);
  loadItinerary();
});
