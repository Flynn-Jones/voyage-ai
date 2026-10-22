"use strict";

// All calls stay behind the owning backend/Nginx boundary.
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("multi-agent-form");
  const status = document.getElementById("multi-agent-status");
  const output = document.getElementById("multi-agent-output");
  const controls = document.getElementById("multi-agent-controls");
  const proposals = document.getElementById("multi-agent-proposals");
  const decisionForm = document.getElementById("multi-agent-decision");
  const apply = document.getElementById("multi-agent-apply");
  const refresh = document.getElementById("multi-agent-refresh");
  const runInput = document.getElementById("multi-agent-run-id");
  let run = null;
  let busy = false;

  function buttons() {
    form.querySelector("button").disabled = busy;
    refresh.disabled = busy;
    decisionForm.querySelectorAll("button, input, textarea").forEach((el) => {
      el.disabled = busy || !run || run.state !== "awaiting_human_review";
    });
    apply.disabled = busy || !run || !["approved", "partially_accepted", "corrected"].includes(run.state);
  }

  async function action(work) {
    if (busy) return;
    busy = true;
    buttons();
    status.textContent = "Working…";
    try {
      await work();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      buttons();
    }
  }

  function jsonStage(label, data) {
    const section = makeElement("section", "workflow-stage");
    section.append(makeElement("h3", "workflow-stage__title", label),
      makeElement("pre", "release1-result", JSON.stringify(data, null, 2)));
    return section;
  }

  function render(value) {
    run = value;
    runInput.value = run.run_id;
    controls.hidden = false;
    status.textContent = `Run ${run.run_id} · ${run.state}`;
    output.replaceChildren(jsonStage("Planner", run.plan), jsonStage("Worker", run.work),
      jsonStage("Reviewer", run.review), jsonStage("Human decision / application", {
        decision: run.decision, approved_items: run.approved_items, applied: run.applied, error: run.error,
      }));
    proposals.replaceChildren();
    for (const item of run.work?.items || []) {
      const row = makeElement("fieldset", "multi-agent-proposal");
      row.dataset.itemId = item.id;
      row.append(makeElement("legend", "", `Item ${item.id} · Day ${item.day}`));
      const label = makeElement("label", "multi-agent-select");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = true;
      checkbox.name = "selected";
      label.append(checkbox, document.createTextNode(" Include this proposal"));
      row.append(label, makeElement("p", "", `${item.original.start_time}–${item.original.end_time} → ${item.start_time}–${item.end_time}. ${item.reason}`));
      for (const [name, title, type] of [["start_time", "Start", "time"], ["end_time", "End", "time"], ["notes", "Notes", "text"]]) {
        const field = makeElement("label", "", title);
        const input = document.createElement("input");
        input.type = type;
        input.name = name;
        input.value = item[name] ?? "";
        field.append(input);
        row.append(field);
      }
      proposals.append(row);
    }
    if (!(run.work?.items || []).length) proposals.append(makeElement("p", "", "No time shifts proposed. Review the findings before closing this run."));
  }

  async function inspect(id) {
    const data = await apiRequest(`/multi-agent/runs/${encodeURIComponent(id)}`);
    render(data.result);
    const history = await apiRequest(`/multi-agent/runs/${encodeURIComponent(id)}/history`);
    output.append(jsonStage("Run history", history.result));
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    action(async () => {
      const data = new FormData(form);
      const payload = { trip_reference: data.get("trip_reference").trim() };
      if (data.get("day")) payload.day = Number(data.get("day"));
      const response = await apiRequest("/multi-agent/run", { method: "POST", body: JSON.stringify(payload) });
      render(response.result);
      await inspect(response.result.run_id);
    });
  });

  refresh.addEventListener("click", () => action(async () => {
    await inspect(runInput.value.trim());
  }));

  decisionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const decision = event.submitter.value;
    action(async () => {
      const payload = { decision, approved_ids: [], rejected_ids: [], edits: {}, note: document.getElementById("multi-agent-note").value };
      for (const row of proposals.querySelectorAll("fieldset")) {
        const item = run.work.items.find((i) => String(i.id) === row.dataset.itemId);
        const selected = row.querySelector('[name="selected"]').checked;
        (selected ? payload.approved_ids : payload.rejected_ids).push(item.id);
        if (selected) {
          const edits = {};
          for (const key of ["start_time", "end_time", "notes"]) {
            const value = row.querySelector(`[name="${key}"]`).value;
            if (value !== (item[key] ?? "")) edits[key] = value;
          }
          if (Object.keys(edits).length) payload.edits[item.id] = edits;
        }
      }
      if (decision === "reject") {
        payload.approved_ids = [];
        payload.rejected_ids = [];
        payload.edits = {};
      }
      const response = await apiRequest(`/multi-agent/runs/${encodeURIComponent(run.run_id)}/review`, {
        method: "POST", body: JSON.stringify(payload),
      });
      // Immediately lock the decision controls even if the following refresh fails.
      render({ ...run, ...response.result });
      await inspect(run.run_id);
    });
  });

  apply.addEventListener("click", () => action(async () => {
    const response = await apiRequest(`/multi-agent/runs/${encodeURIComponent(run.run_id)}/apply`, { method: "POST", body: "{}" });
    const message = response.result.message;
    render({ ...run, ...response.result });
    await loadItinerary();
    await inspect(run.run_id);
    status.textContent += ` · ${message}`;
  }));
  buttons();
});
