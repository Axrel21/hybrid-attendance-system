/** Minimal attendance operations dashboard — polling only (D.2B). */

const POLL_MS = 5000;
const API = {
  activeClassrooms: "/attendance/classrooms/active",
  records: (id) => `/attendance/lectures/${id}/records`,
  events: (id) => `/attendance/lectures/${id}/events`,
  logs: (id) => `/attendance/recognition/logs?lecture_id=${id}&limit=50`,
};

let selectedLectureId = null;
let pollTimer = null;

const $ = (sel) => document.querySelector(sel);

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function fmtPct(n, total) {
  if (!total) return "0%";
  return `${Math.round((n / total) * 100)}%`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

function setStatus(ok, msg) {
  const meta = $("#status-meta");
  $("#status-text").textContent = msg;
  meta.classList.toggle("error", !ok);
  if (ok) {
    $("#last-refresh").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  }
}

function statePill(state) {
  const s = (state || "unknown").toLowerCase();
  return `<span class="state-pill ${s}">${s}</span>`;
}

function renderOverview(data) {
  const grid = $("#overview-grid");
  const empty = $("#overview-empty");
  grid.innerHTML = "";

  const entries = data.active_lectures || [];
  if (!entries.length) {
    empty.hidden = false;
    $("#detail").classList.remove("visible");
    selectedLectureId = null;
    return;
  }
  empty.hidden = true;

  entries.forEach((entry) => {
    const lec = entry.lecture;
    const sum = entry.attendance_summary || lec.attendance_summary || {};
    const card = document.createElement("div");
    card.className = "card";
    if (lec.id === selectedLectureId) card.classList.add("selected");
    card.dataset.lectureId = lec.id;
    card.innerHTML = `
      <h3>${entry.classroom_name}</h3>
      <p class="sub">${lec.subject_name} · ${lec.subject_code}</p>
      <div class="counts">
        <span class="badge confirmed">confirmed ${sum.confirmed || 0}</span>
        <span class="badge initialized">init ${sum.initialized || 0}</span>
        <span class="badge candidate">cand ${sum.candidate || 0}</span>
        <span class="badge undetected">undet ${sum.undetected || 0}</span>
        <span class="badge">total ${sum.total_enrolled || 0}</span>
      </div>
    `;
    card.addEventListener("click", () => selectLecture(lec.id, entry));
    grid.appendChild(card);
  });

  if (selectedLectureId) {
    const current = entries.find((e) => e.lecture.id === selectedLectureId);
    if (current) {
      selectLecture(current.lecture.id, current, false);
    } else {
      selectedLectureId = null;
      $("#detail").classList.remove("visible");
    }
  } else if (entries.length) {
    selectLecture(entries[0].lecture.id, entries[0], false);
  }
}

function selectLecture(lectureId, entry, scroll = true) {
  selectedLectureId = lectureId;
  document.querySelectorAll(".card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.lectureId === lectureId);
  });

  const lec = entry.lecture;
  const sum = entry.attendance_summary || lec.attendance_summary || {};
  $("#detail").classList.add("visible");
  $("#detail-title").textContent = `${entry.classroom_name} — ${lec.subject_name}`;
  $("#detail-sub").textContent = [
    lec.subject_code,
    lec.status,
    `confirmed ${sum.confirmed || 0} / ${sum.total_enrolled || 0}`,
    fmtPct(sum.confirmed, sum.total_enrolled),
  ].join(" · ");

  if (scroll) $("#detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderRecords(data) {
  const body = $("#records-body");
  body.innerHTML = "";
  (data.records || []).forEach((r) => {
    const tr = document.createElement("tr");
    const prog = r.progression || {};
    tr.innerHTML = `
      <td>${r.student_name}<br><span class="mono">${r.student_no}</span></td>
      <td>${statePill(r.state)}</td>
      <td>${prog.attendance_event_count ?? 0}</td>
      <td class="mono">${fmtTime(r.last_event_at)}</td>
      <td class="mono">${fmtTime(r.confirmed_at)}</td>
    `;
    body.appendChild(tr);
  });
}

function renderEvents(data) {
  const body = $("#events-body");
  body.innerHTML = "";
  (data.events || []).forEach((e) => {
    const sem = e.semantic === "transition" ? "semantic-transition" : "semantic-accumulation";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${fmtTime(e.created_at)}</td>
      <td>${e.student_name}</td>
      <td class="${sem}">${e.semantic}</td>
      <td class="mono">${e.from_state} → ${e.to_state}</td>
      <td class="mono">${e.source}</td>
    `;
    body.appendChild(tr);
  });
}

function renderLogs(data) {
  const body = $("#logs-body");
  body.innerHTML = "";
  (data.logs || []).forEach((log) => {
    const cls = log.accepted ? "log-accepted" : "log-rejected";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${fmtTime(log.received_at)}</td>
      <td>${log.gallery_identity}</td>
      <td>${(log.confidence * 100).toFixed(0)}%</td>
      <td class="mono">${log.camera_id || "—"}</td>
      <td class="mono">${log.outcome}</td>
      <td class="${cls}">${log.accepted ? "accepted" : "rejected"}</td>
    `;
    body.appendChild(tr);
  });
}

async function refreshDetail() {
  if (!selectedLectureId) return;
  const [records, events, logs] = await Promise.all([
    fetchJson(API.records(selectedLectureId)),
    fetchJson(API.events(selectedLectureId)),
    fetchJson(API.logs(selectedLectureId)),
  ]);
  renderRecords(records);
  renderEvents(events);
  renderLogs(logs);
}

async function refresh() {
  try {
    const overview = await fetchJson(API.activeClassrooms);
    renderOverview(overview);
    await refreshDetail();
    setStatus(true, "Live · polling every 5s");
  } catch (err) {
    console.error(err);
    setStatus(false, `Error: ${err.message}`);
  }
}

function startPolling() {
  refresh();
  pollTimer = setInterval(refresh, POLL_MS);
}

document.addEventListener("DOMContentLoaded", startPolling);
