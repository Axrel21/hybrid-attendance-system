/** Minimal attendance operations dashboard — polling only (D.2B + D5 surveillance). */

const POLL_MS = 5000;
const MAX_TELEMETRY_ROWS = 20;
const API = {
  activeClassrooms: "/attendance/classrooms/active",
  createLecture: "/attendance/lectures",
  lectures: "/attendance/lectures",
  sources: "/attendance/sources",
  startLecture: (id) => `/attendance/lectures/${id}/start`,
  closeLecture: (id) => `/attendance/lectures/${id}/close`,
  finalizeLecture: (id) => `/attendance/lectures/${id}/finalize`,
  records: (id) => `/attendance/lectures/${id}/records`,
  events: (id) => `/attendance/lectures/${id}/events`,
  logs: (id) => `/attendance/recognition/logs?lecture_id=${id}&limit=${MAX_TELEMETRY_ROWS}`,
  presenceSessions: "/presence/sessions",
  evidence: "/attendance/evidence",
  evidenceForLecture: (id) => `/attendance/evidence/${id}`,
  health: "/health",
  healthAttendance: "/health/attendance",
  config: "/system/config",
  report: "/attendance/report",
  sessions: "/api/sessions",
  sessionDetail: (id) => `/api/sessions/${id}`,
  sessionTelemetry: (id, limit, offset) =>
    `/api/sessions/${id}/telemetry?limit=${limit}&offset=${offset}`,
};

const EDGE_STALE_MS = 15000;
const EDGE_TAIL_EVENTS = 40;
const EDGE_MAX_NODES = 6;
const EDGE_DETAIL_FETCH = 20;

let selectedLectureId = null;
let previousLectureId = null;
let pollTimer = null;
let systemMeta = { profile: "—", ready: false, reportTotal: 0 };
let lastPresence = { total: 0, sessions: [] };
let lastEvidence = { total: 0, records: [] };
let lastRecords = { total: 0, records: [] };
let evidenceByStudent = new Map();
let toastTimer = null;
let createFormLoading = false;
let lifecycleActionLoading = false;
let pendingFinalize = null;
let lectureStatusById = new Map();

const $ = (sel) => document.querySelector(sel);

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function fmtTimeCompact(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function truncateMono(value, max = 10) {
  const s = String(value ?? "");
  if (!s || s === "—") return "—";
  if (s.length <= max) return s;
  return `${s.slice(0, max)}…`;
}

function escapeAttr(value) {
  return String(value ?? "").replace(/"/g, "&quot;");
}

function monoCell(value, max = 10) {
  const full = String(value ?? "—");
  const short = truncateMono(full, max);
  if (short === full) return `<span class="mono">${short}</span>`;
  return `<span class="mono cell-truncate" title="${escapeAttr(full)}">${short}</span>`;
}

function setEmptyTitle(el, title) {
  if (!el) return;
  const titleEl = el.querySelector(".empty-state-title");
  if (titleEl) titleEl.textContent = title;
  else el.textContent = title;
}

function fmtPct(n, total) {
  if (!total) return "0%";
  return `${Math.round((n / total) * 100)}%`;
}

function fmtDuration(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n < 0) return "—";
  return `${Math.round(n)}s`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

async function readJsonResponse(res) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

async function requestJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await readJsonResponse(res);
  if (!res.ok) {
    const detail = formatApiError(data?.detail ?? (typeof data === "string" ? data : ""));
    throw new Error(detail || `${res.status} request failed`);
  }
  return data;
}

async function postJson(url, body) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function patchJson(url) {
  return requestJson(url, { method: "PATCH" });
}

function formatApiError(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  return JSON.stringify(detail);
}

function showToast(message, type = "success") {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast ${type}`;
  toast.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 5000);
}

function toDatetimeLocalValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-") + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function defaultCreateFormTimes() {
  const start = new Date();
  start.setSeconds(0, 0);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  $("#create-start").value = toDatetimeLocalValue(start);
  $("#create-end").value = toDatetimeLocalValue(end);
  $("#create-window").value = "15";
}

function collectLectureOptions(activeData, lecturesData, sourcesData) {
  const subjects = new Map();
  const classrooms = new Map();

  const addSubject = (id, code, name) => {
    if (!id) return;
    subjects.set(String(id), { id: String(id), code: code || "", name: name || id });
  };

  const addClassroom = (id, name) => {
    if (!id) return;
    classrooms.set(String(id), { id: String(id), name: name || id });
  };

  (activeData?.active_lectures || []).forEach((entry) => {
    const lec = entry.lecture || {};
    addSubject(lec.subject_id, lec.subject_code, lec.subject_name);
    addClassroom(entry.classroom_id, entry.classroom_name);
  });

  (lecturesData?.lectures || []).forEach((lec) => {
    addSubject(lec.subject_id, lec.subject_code, lec.subject_name);
    addClassroom(lec.classroom_id, lec.classroom_name);
  });

  (sourcesData?.sources || []).forEach((src) => {
    addClassroom(src.classroom_id, src.classroom_name);
  });

  return {
    subjects: [...subjects.values()].sort((a, b) =>
      `${a.code} ${a.name}`.localeCompare(`${b.code} ${b.name}`),
    ),
    classrooms: [...classrooms.values()].sort((a, b) =>
      a.name.localeCompare(b.name),
    ),
  };
}

function populateSelect(select, items, placeholder, formatLabel) {
  const prev = select.value;
  select.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = placeholder;
  select.appendChild(empty);
  items.forEach((item) => {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = formatLabel(item);
    select.appendChild(opt);
  });
  if (prev && items.some((item) => item.id === prev)) {
    select.value = prev;
  }
}

async function loadCreateFormOptions() {
  const subjectSelect = $("#create-subject");
  const classroomSelect = $("#create-classroom");
  const hint = $("#create-form-hint");
  const submit = $("#create-submit");

  subjectSelect.disabled = true;
  classroomSelect.disabled = true;
  submit.disabled = true;

  const [activeRes, lecturesRes, sourcesRes] = await Promise.allSettled([
    fetchJson(API.activeClassrooms),
    fetchJson(`${API.lectures}?limit=500`),
    fetchJson(`${API.sources}?active_only=false`),
  ]);

  const activeData = activeRes.status === "fulfilled" ? activeRes.value : null;
  const lecturesData = lecturesRes.status === "fulfilled" ? lecturesRes.value : null;
  const sourcesData = sourcesRes.status === "fulfilled" ? sourcesRes.value : null;

  const { subjects, classrooms } = collectLectureOptions(activeData, lecturesData, sourcesData);

  populateSelect(subjectSelect, subjects, "Select subject…", (s) =>
    s.code ? `${s.code} — ${s.name}` : s.name,
  );
  populateSelect(classroomSelect, classrooms, "Select classroom…", (c) => c.name);

  const hasSubjects = subjects.length > 0;
  const hasClassrooms = classrooms.length > 0;
  hint.hidden = hasSubjects && hasClassrooms;
  if (!hasSubjects) {
    hint.textContent = "No subjects found in lecture history. Seed subjects in the database first.";
  } else if (!hasClassrooms) {
    hint.textContent = "No classrooms found. Register camera sources or seed classrooms first.";
  }

  subjectSelect.disabled = !hasSubjects;
  classroomSelect.disabled = !hasClassrooms;
  submit.disabled = !hasSubjects || !hasClassrooms;
}

function openCreateModal() {
  const modal = $("#create-modal");
  if (!modal) return;
  defaultCreateFormTimes();
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  loadCreateFormOptions().catch((err) => {
    showToast(`Could not load form options: ${err.message}`, "error");
  });
  $("#create-subject")?.focus();
}

function closeCreateModal() {
  const modal = $("#create-modal");
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  if ($("#finalize-modal")?.hidden !== false) {
    document.body.style.overflow = "";
  }
}

async function fetchOverviewData() {
  const [activeRes, scheduledRes, closedRes] = await Promise.all([
    fetchJson(API.activeClassrooms),
    fetchJson(`${API.lectures}?status=scheduled&limit=500`),
    fetchJson(`${API.lectures}?status=active_window_closed&limit=500`),
  ]);

  const entries = [];
  const seenLectureIds = new Set();
  const seenClassroomIds = new Set();

  const pushEntry = (entry) => {
    const lectureId = String(entry.lecture?.id || "");
    if (!lectureId || seenLectureIds.has(lectureId)) return;
    seenLectureIds.add(lectureId);
    seenClassroomIds.add(String(entry.classroom_id));
    entries.push(entry);
  };

  (activeRes.active_lectures || []).forEach((entry) => {
    pushEntry({
      classroom_id: entry.classroom_id,
      classroom_name: entry.classroom_name,
      lecture: entry.lecture,
      attendance_summary: entry.attendance_summary || entry.lecture?.attendance_summary,
    });
  });

  const appendListed = (lectures) => {
    (lectures || []).forEach((lec) => {
      if (seenClassroomIds.has(String(lec.classroom_id))) return;
      pushEntry({
        classroom_id: lec.classroom_id,
        classroom_name: lec.classroom_name,
        lecture: lec,
        attendance_summary: lec.attendance_summary,
      });
    });
  };

  appendListed(scheduledRes.lectures);
  appendListed(closedRes.lectures);

  return { total: entries.length, active_lectures: entries };
}

async function refreshOverviewOnly() {
  const overview = await fetchOverviewData();
  renderOverview(overview);
}

async function refreshDashboardState() {
  const overview = await fetchOverviewData();
  renderOverview(overview);
  await refreshDetail();
}

async function handleCreateLectureSubmit(event) {
  event.preventDefault();
  if (createFormLoading) return;

  const form = event.target;
  const submit = $("#create-submit");
  const startVal = form.scheduled_start.value;
  const endVal = form.scheduled_end.value;
  const startDate = new Date(startVal);
  const endDate = new Date(endVal);

  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) {
    showToast("Enter valid start and end times.", "error");
    return;
  }
  if (endDate <= startDate) {
    showToast("End time must be after start time.", "error");
    return;
  }

  const payload = {
    subject_id: form.subject_id.value,
    classroom_id: form.classroom_id.value,
    scheduled_start: startDate.toISOString(),
    scheduled_end: endDate.toISOString(),
    attendance_window_minutes: Number(form.attendance_window_minutes.value),
  };

  createFormLoading = true;
  submit.disabled = true;
  submit.textContent = "Creating…";

  try {
    const created = await postJson(API.createLecture, payload);
    closeCreateModal();
    showToast(
      `Lecture created: ${created.subject_code || created.subject_name} in ${created.classroom_name}`,
      "success",
    );
    await refreshOverviewOnly();
    await refreshDetail();
  } catch (err) {
    showToast(err.message || "Failed to create lecture.", "error");
  } finally {
    createFormLoading = false;
    submit.textContent = "Create Lecture";
    submit.disabled = false;
  }
}

function initCreateLectureUI() {
  $("#btn-create-lecture")?.addEventListener("click", openCreateModal);
  $("#create-lecture-form")?.addEventListener("submit", handleCreateLectureSubmit);

  document.querySelectorAll("[data-dismiss='create-modal']").forEach((el) => {
    el.addEventListener("click", closeCreateModal);
  });
}

function initLifecycleUI() {
  $("#finalize-confirm")?.addEventListener("click", confirmFinalizeLecture);

  document.querySelectorAll("[data-dismiss='finalize-modal']").forEach((el) => {
    el.addEventListener("click", closeFinalizeModal);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeFinalizeModal();
    closeCreateModal();
  });
}

function lifecycleBadge(status) {
  const s = (status || "unknown").toLowerCase();
  return `<span class="status-badge lifecycle-badge ${s}">${s}</span>`;
}

function isStartEnabled(status) {
  return status === "scheduled";
}

function isFinalizeEnabled(status) {
  return status === "active_window_open" || status === "active_window_closed";
}

function clearSelectedLecture() {
  onLectureChanged(null);
  selectedLectureId = null;
  $("#detail").classList.remove("visible");
  clearDetailPanels();
  renderSummaryCards();
}

function openFinalizeModal(entry) {
  const lec = entry.lecture;
  pendingFinalize = {
    lectureId: lec.id,
    status: lec.status,
    label: `${entry.classroom_name} — ${lec.subject_name}`,
  };

  const modal = $("#finalize-modal");
  const text = $("#finalize-modal-text");
  if (text) {
    text.textContent =
      `Finalize "${pendingFinalize.label}"? It will be removed from active dashboard views. Historical attendance and evidence records are preserved.`;
  }
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  $("#finalize-confirm")?.focus();
}

function closeFinalizeModal() {
  const modal = $("#finalize-modal");
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  pendingFinalize = null;
  if ($("#create-modal")?.hidden !== false) {
    document.body.style.overflow = "";
  }
}

async function handleStartLecture(lectureId, entry) {
  if (lifecycleActionLoading) return;
  lifecycleActionLoading = true;

  try {
    const updated = await patchJson(API.startLecture(lectureId));
    showToast(
      `Lecture started: ${updated.subject_code || updated.subject_name} in ${updated.classroom_name}`,
      "success",
    );
    await refreshDashboardState();
    if (updated.id) {
      selectLecture(updated.id, {
        classroom_id: updated.classroom_id,
        classroom_name: updated.classroom_name,
        lecture: updated,
        attendance_summary: updated.attendance_summary,
      }, false);
    }
  } catch (err) {
    showToast(err.message || "Failed to start lecture.", "error");
  } finally {
    lifecycleActionLoading = false;
  }
}

async function confirmFinalizeLecture() {
  if (!pendingFinalize || lifecycleActionLoading) return;

  const { lectureId, status, label } = pendingFinalize;
  lifecycleActionLoading = true;
  const confirmBtn = $("#finalize-confirm");
  const prevLabel = confirmBtn?.textContent;
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Finalizing…";
  }

  try {
    if (status === "active_window_open") {
      await patchJson(API.closeLecture(lectureId));
    }
    await patchJson(API.finalizeLecture(lectureId));
    closeFinalizeModal();
    showToast(`Lecture finalized: ${label}`, "success");

    if (String(selectedLectureId) === String(lectureId)) {
      clearSelectedLecture();
    }

    await refreshDashboardState();
  } catch (err) {
    showToast(err.message || "Failed to finalize lecture.", "error");
  } finally {
    lifecycleActionLoading = false;
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = prevLabel || "Finalize";
    }
  }
}

function setStatus(ok, msg) {
  const meta = $("#status-meta");
  const suffix = ` · ${systemMeta.profile} · reports ${systemMeta.reportTotal}`;
  $("#status-text").textContent = msg + suffix;
  meta.classList.toggle("error", !ok || !systemMeta.ready);
  if (ok) {
    $("#last-refresh").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  }
}

async function refreshSystem() {
  const [health, config, report] = await Promise.allSettled([
    fetchJson(API.health),
    fetchJson(API.config),
    fetchJson(API.report),
  ]);
  if (health.status === "fulfilled") {
    systemMeta.ready = Boolean(health.value.ready);
    systemMeta.profile = health.value.profile || systemMeta.profile;
  }
  if (config.status === "fulfilled") {
    systemMeta.profile = config.value.profile || systemMeta.profile;
  }
  if (report.status === "fulfilled") {
    systemMeta.reportTotal = report.value.total ?? 0;
  }
}

function statePill(state) {
  const s = (state || "unknown").toLowerCase();
  return `<span class="status-badge state-pill ${s}">${s}</span>`;
}

function statusPill(status) {
  const s = (status || "inactive").toLowerCase();
  return `<span class="status-badge status-pill ${s}">${s}</span>`;
}

function evidencePill(evidence) {
  const e = (evidence || "unknown").toLowerCase();
  return `<span class="status-badge evidence-pill ${e}">${e.replace(/_/g, " ")}</span>`;
}

function filterEvidenceForLecture(records, lectureId) {
  const rows = records || [];
  if (!lectureId) return rows;
  const key = String(lectureId);
  return rows.filter((r) => !r.lecture_id || String(r.lecture_id) === key);
}

function buildEvidenceIndex(records, lectureId) {
  const index = new Map();
  const filtered = filterEvidenceForLecture(records, lectureId);
  filtered.forEach((row) => {
    const sid = String(row.student_id || "");
    if (!sid) return;
    const prev = index.get(sid);
    if (!prev || (row.presence_duration_sec || 0) >= (prev.presence_duration_sec || 0)) {
      index.set(sid, row);
    }
  });
  return index;
}

function studentKeys(record) {
  const keys = [];
  if (record.student_id) keys.push(String(record.student_id));
  if (record.gallery_identity) keys.push(String(record.gallery_identity));
  return keys;
}

function dedupeClassroomEntries(entries) {
  const seen = new Set();
  return (entries || []).filter((entry) => {
    const id = String(entry.lecture?.id || entry.classroom_id);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function lookupEvidence(record) {
  for (const key of studentKeys(record)) {
    if (evidenceByStudent.has(key)) return evidenceByStudent.get(key);
  }
  return null;
}

function presenceLabel(record) {
  const ev = lookupEvidence(record);
  if (ev?.evidence === "presence_observed") {
    return `<div class="presence-badge observed">presence active</div>`;
  }
  return `<div class="presence-badge no-presence">presence inactive</div>`;
}

function resetPresenceState() {
  lastPresence = { total: 0, sessions: [] };
  evidenceByStudent = new Map();
  lastEvidence = { total: 0, records: [] };
  renderPresenceSessions({ total: 0, sessions: [] });
  renderEvidencePanel({ records: [] });
  $("#sum-active-tracks").textContent = "0";
  $("#sum-observed-students").textContent = "0";
  $("#sum-presence-coverage").textContent = "0%";
  if (lastRecords.records?.length) {
    renderRecords(lastRecords);
  }
}

function renderSummaryCards() {
  if ((lastPresence?.total ?? 0) === 0) {
    $("#sum-active-tracks").textContent = "0";
    $("#sum-observed-students").textContent = "0";
    $("#sum-presence-coverage").textContent = "0%";
    return;
  }

  const sessions = lastPresence.sessions || [];
  const activeTracks = sessions.length;
  $("#sum-active-tracks").textContent = String(activeTracks);

  const evRows = filterEvidenceForLecture(lastEvidence.records, selectedLectureId);
  const observedIds = new Set(
    evRows
      .filter((e) => e.evidence === "presence_observed")
      .map((e) => String(e.student_id)),
  );
  $("#sum-observed-students").textContent = String(observedIds.size);

  const enrolled = (lastRecords.records || []).length;
  if (selectedLectureId && enrolled > 0) {
    $("#sum-presence-coverage").textContent = fmtPct(observedIds.size, enrolled);
  } else if (observedIds.size > 0 && evRows.length > 0) {
    $("#sum-presence-coverage").textContent = fmtPct(
      observedIds.size,
      new Set(evRows.map((e) => String(e.student_id))).size,
    );
  } else {
    $("#sum-presence-coverage").textContent = enrolled ? "0%" : "—";
  }
}

function renderPresenceSessions(data) {
  const body = $("#presence-body");
  const empty = $("#presence-empty");
  const sessions = data?.sessions || [];
  body.innerHTML = "";

  if (!sessions.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  sessions.forEach((s) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${s.track_id}</td>
      <td class="mono">${s.camera_id || "—"}</td>
      <td class="mono">${fmtDuration(s.duration_sec)}</td>
      <td>${statusPill(s.status)}</td>
    `;
    body.appendChild(tr);
  });
}

function renderEvidencePanel(data) {
  const body = $("#evidence-body");
  const empty = $("#evidence-empty");
  const records = filterEvidenceForLecture(data?.records || [], selectedLectureId);
  body.innerHTML = "";

  if (!records.length) {
    empty.hidden = false;
    setEmptyTitle(
      empty,
      selectedLectureId
        ? "No surveillance evidence"
        : "Select a lecture to view evidence",
    );
    return;
  }
  empty.hidden = true;

  records.forEach((e) => {
    const tr = document.createElement("tr");
    const track = e.presence_track_id != null ? e.presence_track_id : "—";
    const delta = e.time_delta_sec != null ? `${e.time_delta_sec}s` : "—";
    tr.innerHTML = `
      <td>${monoCell(e.student_id, 12)}</td>
      <td>${evidencePill(e.evidence)}</td>
      <td>${e.confidence || "—"}</td>
      <td class="mono">${track}</td>
      <td class="mono">${fmtDuration(e.presence_duration_sec)}</td>
      <td class="mono">${delta}</td>
    `;
    body.appendChild(tr);
  });
}

function clearDetailPanels() {
  evidenceByStudent = new Map();
  lastEvidence = { total: 0, records: [] };
  lastRecords = { total: 0, records: [] };
  renderRecords({ records: [] });
  renderEvents({ events: [] });
  renderLogs({ logs: [] });
  renderEvidencePanel({ records: [] });
}

function onLectureChanged(lectureId) {
  if (previousLectureId !== null && previousLectureId !== lectureId) {
    clearDetailPanels();
  }
  previousLectureId = lectureId;
}

function renderOverview(data) {
  const grid = $("#overview-grid");
  const empty = $("#overview-empty");
  grid.innerHTML = "";

  const entries = dedupeClassroomEntries(data.active_lectures || []);
  lectureStatusById.clear();
  entries.forEach((entry) => {
    lectureStatusById.set(String(entry.lecture.id), (entry.lecture.status || "").toLowerCase());
  });
  if (!entries.length) {
    empty.hidden = false;
    $("#detail").classList.remove("visible");
    if (selectedLectureId !== null) {
      onLectureChanged(null);
      selectedLectureId = null;
      clearDetailPanels();
    }
    renderSummaryCards();
    return;
  }
  empty.hidden = true;

  entries.forEach((entry) => {
    const lec = entry.lecture;
    const sum = entry.attendance_summary || lec.attendance_summary || {};
    const status = (lec.status || "").toLowerCase();
    const startDisabled = !isStartEnabled(status);
    const finalizeDisabled = !isFinalizeEnabled(status);
    const card = document.createElement("div");
    card.className = "card";
    if (lec.id === selectedLectureId) card.classList.add("selected");
    card.dataset.lectureId = lec.id;
    card.dataset.classroomId = entry.classroom_id;
    card.dataset.lectureStatus = status;
    card.innerHTML = `
      <div class="card-head">
        <div>
          <h3>${entry.classroom_name}</h3>
          <p class="sub">${lec.subject_name} · ${lec.subject_code}</p>
        </div>
        ${lifecycleBadge(status)}
      </div>
      <div class="counts">
        <span class="badge confirmed">confirmed ${sum.confirmed || 0}</span>
        <span class="badge initialized">init ${sum.initialized || 0}</span>
        <span class="badge candidate">cand ${sum.candidate || 0}</span>
        <span class="badge undetected">undet ${sum.undetected || 0}</span>
        <span class="badge">total ${sum.total_enrolled || 0}</span>
      </div>
      <div class="card-actions">
        <button type="button" class="btn btn-sm btn-primary btn-start"${startDisabled ? " disabled" : ""}>Start</button>
        <button type="button" class="btn btn-sm btn-ghost btn-finalize"${finalizeDisabled ? " disabled" : ""}>Finalize</button>
      </div>
    `;

    card.querySelector(".btn-start")?.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!startDisabled) handleStartLecture(lec.id, entry);
    });
    card.querySelector(".btn-finalize")?.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!finalizeDisabled) openFinalizeModal(entry);
    });
    card.addEventListener("click", () => selectLecture(lec.id, entry));
    grid.appendChild(card);
  });

  if (selectedLectureId) {
    const current = entries.find((e) => e.lecture.id === selectedLectureId);
    if (current) {
      selectLecture(current.lecture.id, current, false);
    } else {
      onLectureChanged(null);
      selectedLectureId = null;
      $("#detail").classList.remove("visible");
      clearDetailPanels();
    }
  } else if (entries.length) {
    const preferred = entries.find((e) => e.lecture.status === "active_window_open") || entries[0];
    selectLecture(preferred.lecture.id, preferred, false);
  }
}

function selectLecture(lectureId, entry, scroll = true) {
  onLectureChanged(lectureId);
  selectedLectureId = lectureId;
  document.querySelectorAll(".card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.lectureId === lectureId);
  });

  const lec = entry.lecture;
  const status = (lec.status || "").toLowerCase();
  const sum = entry.attendance_summary || lec.attendance_summary || {};

  if (status !== "active_window_open") {
    $("#detail").classList.remove("visible");
    clearDetailPanels();
    renderSummaryCards();
    if (scroll) return;
    return;
  }

  $("#detail").classList.add("visible");
  $("#detail-title").textContent = `${entry.classroom_name} — ${lec.subject_name}`;
  $("#detail-sub").textContent = [
    lec.subject_code,
    lec.status,
    `confirmed ${sum.confirmed || 0} / ${sum.total_enrolled || 0}`,
    fmtPct(sum.confirmed, sum.total_enrolled),
  ].join(" · ");

  evidenceByStudent =
    (lastPresence?.total ?? 0) === 0
      ? new Map()
      : buildEvidenceIndex(lastEvidence.records, selectedLectureId);
  renderSummaryCards();
  renderEvidencePanel((lastPresence?.total ?? 0) === 0 ? { records: [] } : lastEvidence);

  if (scroll) $("#detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderRecords(data) {
  const body = $("#records-body");
  const empty = $("#records-empty");
  body.innerHTML = "";
  const rows = data?.records || [];
  if (!rows.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    const prog = r.progression || {};
    tr.innerHTML = `
      <td>${r.student_name}<br><span class="mono">${r.student_no}</span></td>
      <td><div class="state-stack">${statePill(r.state)}${presenceLabel(r)}</div></td>
      <td>${prog.attendance_event_count ?? 0}</td>
      <td class="mono cell-time">${fmtTimeCompact(r.last_event_at)}</td>
      <td class="mono cell-time">${fmtTimeCompact(r.confirmed_at)}</td>
    `;
    body.appendChild(tr);
  });
}

function renderEvents(data) {
  const body = $("#events-body");
  const empty = $("#events-empty");
  body.innerHTML = "";
  const rows = (data?.events || []).slice(0, MAX_TELEMETRY_ROWS);
  if (!rows.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  rows.forEach((e) => {
    const sem = e.semantic === "transition" ? "semantic-transition" : "semantic-accumulation";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono cell-time">${fmtTimeCompact(e.created_at)}</td>
      <td>${e.student_name}</td>
      <td class="${sem}">${e.semantic}</td>
      <td class="mono">${e.from_state} → ${e.to_state}</td>
      <td>${monoCell(e.source, 14)}</td>
    `;
    body.appendChild(tr);
  });
}

function renderLogs(data) {
  const body = $("#logs-body");
  const empty = $("#logs-empty");
  body.innerHTML = "";
  const rows = (data?.logs || []).slice(0, MAX_TELEMETRY_ROWS);
  if (!rows.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  rows.forEach((log) => {
    const cls = log.accepted ? "log-accepted" : "log-rejected";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono cell-time">${fmtTimeCompact(log.received_at)}</td>
      <td>${log.gallery_identity}</td>
      <td>${(log.confidence * 100).toFixed(0)}%</td>
      <td class="mono">${log.camera_id || "—"}</td>
      <td>${monoCell(log.outcome, 16)}</td>
      <td class="${cls}">${log.accepted ? "accepted" : "rejected"}</td>
    `;
    body.appendChild(tr);
  });
}

async function refreshSurveillanceGlobal() {
  const presence = await fetchJson(API.presenceSessions);
  lastPresence = presence;
  if ((presence?.total ?? 0) === 0) {
    resetPresenceState();
    return;
  }
  renderPresenceSessions(presence);
}

function eventFields(ev) {
  return ev?.fields && typeof ev.fields === "object" ? ev.fields : {};
}

function toNum(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatNodeLabel(deviceId) {
  if (!deviceId) return "Edge node";
  const s = String(deviceId);
  if (s.length <= 18) return s;
  return `${s.slice(0, 16)}…`;
}

function formatRecognitionMode(mode) {
  if (mode === "cloud-heavy") return "Cloud-heavy";
  if (mode === "hybrid") return "Hybrid";
  if (mode === "local") return "Local";
  return "—";
}

function mapRecognitionMode(cloudSrc, localSrc, offloadPct) {
  const total = cloudSrc + localSrc;
  if (total > 0) {
    const cloudFrac = cloudSrc / total;
    if (cloudFrac >= 0.55) return "cloud-heavy";
    if (cloudFrac >= 0.15) return "hybrid";
    return "local";
  }
  if (offloadPct != null) {
    if (offloadPct >= 50) return "cloud-heavy";
    if (offloadPct >= 15) return "hybrid";
    return "local";
  }
  return "hybrid";
}

function computeEdgeHealth(metrics) {
  if (!metrics.online) return "offline";
  const temp = metrics.temp;
  const fps = metrics.fps;
  if ((temp != null && temp >= 75) || (fps != null && fps > 0 && fps < 4)) return "degraded";
  if ((temp != null && temp >= 65) || (fps != null && fps > 0 && fps < 6)) return "warning";
  return "healthy";
}

function emptyEdgeMetrics() {
  return {
    online: false,
    recognitionMode: null,
    fps: null,
    temp: null,
    fan: null,
    offloadPct: null,
    avgLatencyMs: null,
    health: "offline",
  };
}

function computeMetricsFromEvents(events) {
  let fps = null;
  let temp = null;
  let fan = null;
  let offloadCount = 0;
  let diagCount = 0;
  let cloudSrc = 0;
  let localSrc = 0;
  const rtts = [];
  let lastTs = 0;

  (events || []).forEach((ev) => {
    if (ev.timestamp_ms > lastTs) lastTs = ev.timestamp_ms;
    const f = eventFields(ev);
    const type = ev.event_type || "";

    if (type === "frame_telemetry" || type === "telemetry") {
      const src = String(f.recognition_source || "").toLowerCase();
      if (src === "cloud") cloudSrc += 1;
      else if (src === "local") localSrc += 1;
    }

    if (type === "diagnostic") {
      diagCount += 1;
      if (f.decision === "OFFLOAD_TO_CLOUD") offloadCount += 1;
      const rtt = toNum(f.cloud_rtt_ms);
      if (rtt != null && rtt > 0) rtts.push(rtt);
    }
  });

  for (let i = (events || []).length - 1; i >= 0; i -= 1) {
    const f = eventFields(events[i]);
    const type = events[i].event_type || "";
    if (type !== "frame_telemetry" && type !== "telemetry") continue;
    if (fps == null) fps = toNum(f.fps_rolling);
    if (temp == null) temp = toNum(f.cpu_temp_c);
    if (fan == null && f.fan_state) fan = String(f.fan_state).trim();
    if (fps != null && temp != null && fan) break;
  }

  if (temp === 0) temp = null;

  const offloadPct = diagCount > 0 ? Math.round((offloadCount / diagCount) * 100) : null;
  const avgLatencyMs =
    rtts.length > 0 ? Math.round(rtts.reduce((sum, v) => sum + v, 0) / rtts.length) : null;

  return {
    fps,
    temp,
    fan,
    offloadPct,
    avgLatencyMs,
    recognitionMode: mapRecognitionMode(cloudSrc, localSrc, offloadPct),
    lastTs,
  };
}

function edgeMetricValue(value, suffix = "") {
  if (value == null || value === "") return "—";
  return `${value}${suffix}`;
}

function edgeHealthBadge(health) {
  const h = (health || "offline").toLowerCase();
  return `<span class="status-badge edge-health ${h}">${h}</span>`;
}

function renderEdgeRuntime(cards) {
  const grid = $("#edge-runtime-grid");
  const empty = $("#edge-runtime-empty");
  if (!grid || !empty) return;

  grid.innerHTML = "";
  if (!cards.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  cards.forEach((node) => {
    const card = document.createElement("article");
    card.className = "edge-card";
    card.innerHTML = `
      <div class="edge-card-head">
        <h3>${formatNodeLabel(node.deviceId)}</h3>
        ${edgeHealthBadge(node.health)}
      </div>
      <dl class="edge-metrics">
        <div><dt>Mode</dt><dd>${formatRecognitionMode(node.recognitionMode)}</dd></div>
        <div><dt>FPS</dt><dd>${edgeMetricValue(node.fps != null ? node.fps.toFixed(1) : null)}</dd></div>
        <div><dt>CPU</dt><dd>${edgeMetricValue(node.temp != null ? Math.round(node.temp) : null, node.temp != null ? "°C" : "")}</dd></div>
        <div><dt>Fan</dt><dd>${node.fan ? node.fan.toUpperCase() : "—"}</dd></div>
        <div><dt>Offload</dt><dd>${edgeMetricValue(node.offloadPct, node.offloadPct != null ? "%" : "")}</dd></div>
        <div><dt>Latency</dt><dd>${edgeMetricValue(node.avgLatencyMs, node.avgLatencyMs != null ? " ms" : "")}</dd></div>
      </dl>
    `;
    grid.appendChild(card);
  });
}

async function buildEdgeNodeCard(deviceId, detail, sessionRow) {
  const base = { deviceId, ...emptyEdgeMetrics() };
  const sessionId = detail.session_id;
  const ended = Boolean(sessionRow?.ended_at || detail.summary?.ended_at);
  const eventCount = detail.event_count || 0;

  if (ended || eventCount === 0) {
    return base;
  }

  const offset = Math.max(0, eventCount - EDGE_TAIL_EVENTS);
  let telemetry;
  try {
    telemetry = await fetchJson(
      API.sessionTelemetry(sessionId, EDGE_TAIL_EVENTS, offset),
    );
  } catch {
    return base;
  }

  const events = telemetry?.events || [];
  if (!events.length) return base;

  const parsed = computeMetricsFromEvents(events);
  const ageMs = parsed.lastTs ? Date.now() - parsed.lastTs : Infinity;
  if (ageMs > EDGE_STALE_MS) {
    return base;
  }

  const metrics = {
    deviceId,
    online: true,
    recognitionMode: parsed.recognitionMode,
    fps: parsed.fps,
    temp: parsed.temp,
    fan: parsed.fan,
    offloadPct: parsed.offloadPct,
    avgLatencyMs: parsed.avgLatencyMs,
    health: "healthy",
  };
  metrics.health = computeEdgeHealth(metrics);
  return metrics;
}

async function refreshEdgeRuntime() {
  try {
    const listRes = await fetchJson(`${API.sessions}?limit=100`);
    const sessions = listRes?.sessions || [];
    if (!sessions.length) {
      renderEdgeRuntime([]);
      return;
    }

    const sorted = [...sessions].sort((a, b) =>
      (b.started_at || b.session_id || "").localeCompare(a.started_at || a.session_id || ""),
    );

    const detailResults = await Promise.allSettled(
      sorted.slice(0, EDGE_DETAIL_FETCH).map((row) => fetchJson(API.sessionDetail(row.session_id))),
    );

    const byDevice = new Map();
    detailResults.forEach((result, idx) => {
      if (result.status !== "fulfilled") return;
      const detail = result.value;
      const sessionRow = sorted[idx];
      const meta = detail.metadata || {};
      const deviceId = meta.device_id || meta.hostname || detail.session_id;
      const prev = byDevice.get(deviceId);
      const started = sessionRow?.started_at || "";
      if (!prev || started > (prev.sessionRow?.started_at || "")) {
        byDevice.set(deviceId, { deviceId, detail, sessionRow });
      }
    });

    const nodes = [...byDevice.values()].slice(0, EDGE_MAX_NODES);
    if (!nodes.length) {
      renderEdgeRuntime([]);
      return;
    }

    const cards = await Promise.all(
      nodes.map(({ deviceId, detail, sessionRow }) =>
        buildEdgeNodeCard(deviceId, detail, sessionRow),
      ),
    );
    renderEdgeRuntime(cards);
  } catch {
    renderEdgeRuntime([]);
  }
}

function shouldPollLectureDetail() {
  if (!selectedLectureId) return false;
  const status = lectureStatusById.get(String(selectedLectureId)) || "";
  return status === "active_window_open";
}

async function refreshDetail() {
  if (!selectedLectureId) {
    clearDetailPanels();
    renderSummaryCards();
    return;
  }

  if (!shouldPollLectureDetail()) {
    clearDetailPanels();
    renderSummaryCards();
    return;
  }

  const presenceEmpty = (lastPresence?.total ?? 0) === 0;

  const detailFetches = [
    fetchJson(API.records(selectedLectureId)),
    fetchJson(API.events(selectedLectureId)),
    fetchJson(API.logs(selectedLectureId)),
  ];
  if (!presenceEmpty) {
    detailFetches.push(fetchJson(API.evidenceForLecture(selectedLectureId)));
  }

  const [records, events, logs, evidence] = await Promise.all(detailFetches);

  lastRecords = records;
  renderRecords(records);
  renderEvents(events);
  renderLogs(logs);

  if (presenceEmpty) {
    lastEvidence = { total: 0, records: [] };
    evidenceByStudent = new Map();
    renderEvidencePanel({ records: [] });
  } else {
    lastEvidence = evidence;
    evidenceByStudent = buildEvidenceIndex(evidence.records, selectedLectureId);
    renderEvidencePanel(evidence);
  }

  renderSummaryCards();
}

async function refresh() {
  try {
    await refreshSystem();
    const overview = await fetchOverviewData();
    await refreshSurveillanceGlobal();
    await refreshEdgeRuntime();
    renderOverview(overview);
    await refreshDetail();
    const live = systemMeta.ready ? "Live" : "Degraded";
    setStatus(true, `${live} · polling every 5s`);
  } catch (err) {
    console.error(err);
    setStatus(false, `Error: ${err.message}`);
  }
}

function startPolling() {
  refresh();
  pollTimer = setInterval(refresh, POLL_MS);
}

document.addEventListener("DOMContentLoaded", () => {
  initCreateLectureUI();
  initLifecycleUI();
  startPolling();
});
