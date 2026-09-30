// Custom date/time picker (replaces the native datetime-local popup, which
// cannot be restyled in any browser). Pure UI widget — unrelated to
// data-fetching, so it stays hand-written JS rather than HTMX; the hidden
// input's initial value (if any) is rendered server-side by Jinja for the
// edit-mode case, and this script picks that up on init.
(function initDatePicker() {
  const hiddenInput = document.getElementById("assignment_time");
  const displayInput = document.getElementById("assignment_time_display");
  const panel = document.getElementById("assignment_time_panel");
  const wrapper = document.getElementById("assignment_time_wrapper");
  if (!hiddenInput || !displayInput || !panel || !wrapper) return;

  const monthLabel = document.getElementById("dp-month-label");
  const daysGrid = document.getElementById("dp-days");
  const hourInput = document.getElementById("dp-hour");
  const minuteInput = document.getElementById("dp-minute");
  const amBtn = document.getElementById("dp-am");
  const pmBtn = document.getElementById("dp-pm");

  const MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  let selected = null; // { year, month (0-11), day, hour24, minute }
  let viewYear;
  let viewMonth;
  const today = new Date();
  viewYear = today.getFullYear();
  viewMonth = today.getMonth();

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function to12Hour(hour24) {
    const ampm = hour24 >= 12 ? "PM" : "AM";
    let hour12 = hour24 % 12;
    if (hour12 === 0) hour12 = 12;
    return { hour12, ampm };
  }

  function to24Hour(hour12, ampm) {
    let hour24 = hour12 % 12;
    if (ampm === "PM") hour24 += 12;
    return hour24;
  }

  function formatISO(s) {
    return `${s.year}-${pad(s.month + 1)}-${pad(s.day)}T${pad(s.hour24)}:${pad(s.minute)}`;
  }

  function formatDisplay(s) {
    const { hour12, ampm } = to12Hour(s.hour24);
    return `${MONTH_NAMES[s.month].slice(0, 3)} ${s.day}, ${s.year} · ${hour12}:${pad(s.minute)} ${ampm}`;
  }

  function updateOutputs() {
    if (!selected) {
      hiddenInput.value = "";
      displayInput.value = "";
      return;
    }
    hiddenInput.value = formatISO(selected);
    displayInput.value = formatDisplay(selected);
  }

  function updateAmPmButtons() {
    const ampm = selected ? to12Hour(selected.hour24).ampm : "AM";
    amBtn.classList.toggle("is-active", ampm === "AM");
    pmBtn.classList.toggle("is-active", ampm === "PM");
  }

  function updateTimeInputs() {
    if (!selected) {
      hourInput.value = "";
      minuteInput.value = "";
      updateAmPmButtons();
      return;
    }
    hourInput.value = to12Hour(selected.hour24).hour12;
    minuteInput.value = pad(selected.minute);
    updateAmPmButtons();
  }

  function renderCalendar() {
    monthLabel.textContent = `${MONTH_NAMES[viewMonth]} ${viewYear}`;
    daysGrid.innerHTML = "";

    const firstWeekday = new Date(viewYear, viewMonth, 1).getDay();
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();

    const cells = [];
    for (let i = firstWeekday - 1; i >= 0; i--) {
      cells.push({ day: daysInPrevMonth - i, muted: true });
    }
    for (let d = 1; d <= daysInMonth; d++) {
      cells.push({ day: d, muted: false });
    }
    while (cells.length % 7 !== 0 || cells.length < 42) {
      cells.push({ day: cells.length - (firstWeekday + daysInMonth) + 1, muted: true });
      if (cells.length >= 42) break;
    }

    cells.forEach((cell) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = cell.day;
      btn.className = "act-datepicker__day" + (cell.muted ? " is-muted" : "");
      const isSelected = selected && !cell.muted &&
        selected.year === viewYear && selected.month === viewMonth && selected.day === cell.day;
      if (isSelected) btn.classList.add("is-selected");
      const isToday = !cell.muted && viewYear === today.getFullYear() &&
        viewMonth === today.getMonth() && cell.day === today.getDate();
      if (isToday && !isSelected) btn.classList.add("is-today");
      if (!cell.muted) {
        btn.addEventListener("click", () => {
          selected = {
            year: viewYear,
            month: viewMonth,
            day: cell.day,
            hour24: selected ? selected.hour24 : 9,
            minute: selected ? selected.minute : 0,
          };
          updateOutputs();
          updateTimeInputs();
          renderCalendar();
        });
      } else {
        btn.disabled = true;
      }
      daysGrid.appendChild(btn);
    });
  }

  function openPanel() {
    if (selected) {
      viewYear = selected.year;
      viewMonth = selected.month;
    }
    renderCalendar();
    updateTimeInputs();
    panel.hidden = false;
  }

  function closePanel() {
    panel.hidden = true;
  }

  displayInput.addEventListener("click", () => {
    if (panel.hidden) openPanel(); else closePanel();
  });

  // This whole script re-runs every time htmx swaps the form fragment back
  // in (e.g. after a validation error), since the <script> tag is part of
  // the swapped content. Element-level listeners above are fine — old
  // elements are discarded with their listeners attached. But `document`
  // itself is never replaced, so document-level listeners would otherwise
  // pile up across swaps; a stale one from an earlier run still closured
  // over the old (now-detached) wrapper/panel would wrongly treat clicks on
  // the *current* picker as "outside" and re-close it immediately after it
  // opens. Attach these exactly once, and resolve the current wrapper/panel
  // by ID at event time rather than from this run's closure, so a single
  // listener keeps working correctly no matter how many times the form
  // fragment gets swapped after this.
  if (!window.__voyageDatepickerGlobalListenersAttached) {
    window.__voyageDatepickerGlobalListenersAttached = true;

    document.addEventListener("click", (event) => {
      const currentWrapper = document.getElementById("assignment_time_wrapper");
      const currentPanel = document.getElementById("assignment_time_panel");
      if (!currentWrapper || !currentPanel) return;
      // composedPath() reflects the DOM at dispatch time; event.target may
      // already be detached by the time this bubbles here (day clicks
      // rebuild the day grid synchronously), which would make
      // currentWrapper.contains(target) wrongly report false.
      if (!event.composedPath().includes(currentWrapper)) currentPanel.hidden = true;
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const currentPanel = document.getElementById("assignment_time_panel");
      if (currentPanel) currentPanel.hidden = true;
    });
  }

  document.getElementById("dp-prev").addEventListener("click", () => {
    viewMonth -= 1;
    if (viewMonth < 0) { viewMonth = 11; viewYear -= 1; }
    renderCalendar();
  });

  document.getElementById("dp-next").addEventListener("click", () => {
    viewMonth += 1;
    if (viewMonth > 11) { viewMonth = 0; viewYear += 1; }
    renderCalendar();
  });

  function applyTimeFromInputs() {
    if (!selected) return;
    let hour12 = parseInt(hourInput.value, 10);
    let minute = parseInt(minuteInput.value, 10);
    if (Number.isNaN(hour12) || hour12 < 1 || hour12 > 12) hour12 = to12Hour(selected.hour24).hour12;
    if (Number.isNaN(minute) || minute < 0 || minute > 59) minute = selected.minute;
    const ampm = to12Hour(selected.hour24).ampm;
    selected.hour24 = to24Hour(hour12, ampm);
    selected.minute = minute;
    updateOutputs();
    updateTimeInputs();
  }

  hourInput.addEventListener("change", applyTimeFromInputs);
  minuteInput.addEventListener("change", applyTimeFromInputs);

  amBtn.addEventListener("click", () => {
    if (!selected) return;
    selected.hour24 = to24Hour(to12Hour(selected.hour24).hour12, "AM");
    updateOutputs();
    updateTimeInputs();
  });

  pmBtn.addEventListener("click", () => {
    if (!selected) return;
    selected.hour24 = to24Hour(to12Hour(selected.hour24).hour12, "PM");
    updateOutputs();
    updateTimeInputs();
  });

  document.getElementById("dp-done").addEventListener("click", closePanel);

  document.getElementById("dp-clear").addEventListener("click", () => {
    selected = null;
    updateOutputs();
    updateTimeInputs();
    renderCalendar();
  });

  document.getElementById("dp-today").addEventListener("click", () => {
    const now = new Date();
    selected = {
      year: now.getFullYear(),
      month: now.getMonth(),
      day: now.getDate(),
      hour24: selected ? selected.hour24 : now.getHours(),
      minute: selected ? selected.minute : now.getMinutes(),
    };
    viewYear = selected.year;
    viewMonth = selected.month;
    updateOutputs();
    updateTimeInputs();
    renderCalendar();
  });

  function setFromISO(isoValue) {
    if (!isoValue) return;
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(isoValue);
    if (!match) return;
    selected = {
      year: parseInt(match[1], 10),
      month: parseInt(match[2], 10) - 1,
      day: parseInt(match[3], 10),
      hour24: parseInt(match[4], 10),
      minute: parseInt(match[5], 10),
    };
    viewYear = selected.year;
    viewMonth = selected.month;
    updateOutputs();
    updateTimeInputs();
  }

  // Edit mode: Jinja renders the current assignment_time straight into the
  // hidden input's value attribute — pick that up here instead of an async
  // client-side fetch, since the server already has the data.
  setFromISO(hiddenInput.value);
  renderCalendar();
})();
