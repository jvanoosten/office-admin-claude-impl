"use strict";

const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = new Set(["COMPLETED", "CANCELLED", "ERROR"]);

const STAGE_LABELS = {
  PENDING:                 "Pending",
  GETTING_CALENDAR_EVENTS: "Calendar Worker getting calendar events",
  CREATING_EVENT_PDFS:     "Document Worker creating PDFs",
  PRINTING_EVENT_PDFS:     "Printer Worker printing PDFs",
  CREATING_EMAIL_DRAFTS:   "Mail Worker creating email drafts",
  COMPLETED:               "Completed",
  CANCELLED:               "Cancelled",
  ERROR:                   "Error",
};

const TASK_TYPE_LABELS = {
  PRINT_CALENDAR_EVENTS:    "Print Calendar Events",
  SEND_EMAIL_NOTIFICATIONS: "Send Email Notifications",
};

// task registry: request_id → { task, intervalId }
const taskRegistry = {};

// ── API helpers ───────────────────────────────────────────────────────────────

async function apiPost(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, data: await res.json() };
}

async function apiGet(url) {
  const res = await fetch(url);
  return { status: res.status, data: await res.json() };
}

// ── Date helpers ──────────────────────────────────────────────────────────────

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function tomorrowStr() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ── Task rendering (DOM-safe) ─────────────────────────────────────────────────

function makeEl(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function renderProgress(task) {
  if (task.task_type === "PRINT_CALENDAR_EVENTS") {
    const s = task.stage;
    if (s === "CREATING_EVENT_PDFS" || (task.documents_expected > 0 && s !== "PRINTING_EVENT_PDFS")) {
      return `${task.documents_completed} of ${task.documents_expected} PDFs created`;
    }
    if (s === "PRINTING_EVENT_PDFS" || task.prints_expected > 0) {
      return `${task.prints_completed} of ${task.prints_expected} PDFs printed`;
    }
    return "";
  }
  if (task.task_type === "SEND_EMAIL_NOTIFICATIONS") {
    if (task.emails_expected > 0) {
      const parts = [`${task.emails_completed} of ${task.emails_expected} drafts created`];
      if (task.emails_skipped > 0) parts.push(`${task.emails_skipped} skipped`);
      return parts.join(", ");
    }
    return "";
  }
  return "";
}

function buildTaskElement(task) {
  const isTerminal = TERMINAL_STATUSES.has(task.status);
  const stageLabel = STAGE_LABELS[task.stage] || task.stage;
  const typeLabel = TASK_TYPE_LABELS[task.task_type] || task.task_type;
  const progress = renderProgress(task);
  const shortId = task.request_id.slice(0, 8);

  const el = makeEl("div", `task-row status-${task.status.toLowerCase()}`);
  el.id = `task-${task.request_id}`;

  const header = makeEl("div", "task-header");
  header.appendChild(makeEl("span", "task-type", typeLabel));
  header.appendChild(makeEl("span", "task-date", task.selected_date || ""));
  const idSpan = makeEl("span", "task-id", `#${shortId}`);
  idSpan.title = task.request_id;
  header.appendChild(idSpan);
  el.appendChild(header);

  const body = makeEl("div", "task-body");
  body.appendChild(makeEl("span", "task-status", task.status));
  body.appendChild(makeEl("span", "task-stage", stageLabel));
  if (progress) body.appendChild(makeEl("span", "task-progress", progress));
  if (task.errors && task.errors.length) {
    body.appendChild(makeEl("span", "task-error", task.errors.join(" | ")));
  }
  el.appendChild(body);

  if (!isTerminal) {
    const cancelBtn = makeEl("button", "cancel-btn", "Cancel");
    cancelBtn.dataset.id = task.request_id;
    cancelBtn.addEventListener("click", () => cancelTask(task.request_id));
    el.appendChild(cancelBtn);
  }

  return el;
}

function upsertTaskElement(task) {
  const list = document.getElementById("task-list");
  const existing = document.getElementById(`task-${task.request_id}`);
  const el = buildTaskElement(task);
  if (existing) {
    list.replaceChild(el, existing);
  } else {
    list.prepend(el);
  }
  document.getElementById("no-tasks").style.display = "none";
}

// ── Polling ───────────────────────────────────────────────────────────────────

function startPolling(requestId) {
  const entry = taskRegistry[requestId];
  if (!entry) return;
  entry.intervalId = setInterval(async () => {
    const { status, data } = await apiGet(`/api/office/status/${requestId}`);
    if (status === 200) {
      entry.task = data;
      upsertTaskElement(data);
      if (TERMINAL_STATUSES.has(data.status)) {
        clearInterval(entry.intervalId);
      }
    }
  }, POLL_INTERVAL_MS);
}

// ── Task actions ──────────────────────────────────────────────────────────────

async function cancelTask(requestId) {
  const { data } = await apiPost(`/api/office/cancel/${requestId}`);
  if (data && data.request_id) {
    taskRegistry[requestId].task = data;
    upsertTaskElement(data);
  }
}

async function submitTask(endpoint, selectedDate, errorEl) {
  errorEl.classList.add("hidden");
  errorEl.textContent = "";

  const { status, data } = await apiPost(endpoint, { selected_date: selectedDate });

  if (status === 202) {
    const requestId = data.request_id;
    const seedTask = {
      request_id: requestId,
      task_type: endpoint.includes("print") ? "PRINT_CALENDAR_EVENTS" : "SEND_EMAIL_NOTIFICATIONS",
      status: "PENDING",
      stage: "PENDING",
      selected_date: selectedDate,
      calendar_event_count: 0,
      events_retrieved: false,
      cancel_requested: false,
      errors: [],
      documents_expected: 0,
      documents_completed: 0,
      prints_expected: 0,
      prints_completed: 0,
      emails_expected: 0,
      emails_completed: 0,
      emails_skipped: 0,
      draft_ids: [],
      skipped_event_ids: [],
    };
    taskRegistry[requestId] = { task: seedTask, intervalId: null };
    upsertTaskElement(seedTask);
    startPolling(requestId);
  } else if (status === 400) {
    errorEl.textContent = "Invalid date.";
    errorEl.classList.remove("hidden");
  } else if (status === 429) {
    errorEl.textContent = "Server busy, please try again.";
    errorEl.classList.remove("hidden");
  } else {
    errorEl.textContent = "An error occurred. Please try again.";
    errorEl.classList.remove("hidden");
  }
}

// ── TASK_ACTIONS map (extensible for future task types) ───────────────────────

const TASK_ACTIONS = {
  PRINT_CALENDAR_EVENTS: {
    buttonId:    "btn-print",
    formId:      "form-print",
    dateInputId: "date-print",
    submitId:    "submit-print",
    errorId:     "error-print",
    endpoint:    "/api/office/print-calendar-events",
    defaultDate: todayStr,
  },
  SEND_EMAIL_NOTIFICATIONS: {
    buttonId:    "btn-email",
    formId:      "form-email",
    dateInputId: "date-email",
    submitId:    "submit-email",
    errorId:     "error-email",
    endpoint:    "/api/office/send-email-notifications",
    defaultDate: tomorrowStr,
  },
};

// ── Wire up UI ────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  for (const action of Object.values(TASK_ACTIONS)) {
    const btn      = document.getElementById(action.buttonId);
    const form     = document.getElementById(action.formId);
    const dateInput = document.getElementById(action.dateInputId);
    const submitBtn = document.getElementById(action.submitId);
    const errorEl  = document.getElementById(action.errorId);

    btn.addEventListener("click", () => {
      dateInput.value = action.defaultDate();
      form.classList.toggle("hidden");
    });

    submitBtn.addEventListener("click", () => {
      if (!dateInput.value) {
        errorEl.textContent = "Please select a date.";
        errorEl.classList.remove("hidden");
        return;
      }
      form.classList.add("hidden");
      submitTask(action.endpoint, dateInput.value, errorEl);
    });
  }
});
