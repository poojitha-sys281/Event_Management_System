const API = "/api";
let token = localStorage.getItem("ems_token") || null;
let user = null;
let mode = "login";          // login | signup
let selectedRole = "attendee";
let editingEventId = null;
let orgEvents = [];
let orgPage = 1, browsePage = 1, regPage = 1;
let resetToken = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const show = (el, on) => el.classList.toggle("hidden", !on);

function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.style.background = isError ? "#b3392c" : "#1b2230";
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && token) { logout(true); throw new Error(data.error || "Please log in"); }
  if (!res.ok) { const er = new Error(data.error || "Request failed"); er.code = data.code; throw er; }
  return data;
}
const run = (fn) => (...a) => fn(...a).catch(e => toast(e.message, true));

// ---------------- Auth screen ----------------
const ROLE_HINT = {
  attendee: "Attendees can browse events and register for them.",
  organizer: "Event creators can create events, set an attendee limit and track registrations.",
};
function notice(text, offerResend = false) {
  $("auth-msg").textContent = text;
  show($("auth-msg"), true);
  show($("resend-row"), offerResend);
}
function renderAuth() {
  const titles = { login: "Log in", signup: "Create your account", forgot: "Reset your password" };
  $("auth-title").textContent = titles[mode];
  $("a-submit").textContent = mode === "login" ? "Log in as " + roleLabel(selectedRole)
    : mode === "signup" ? "Sign up as " + roleLabel(selectedRole) : "Send reset link";
  show($("f-name"), mode === "signup");
  show($("f-pass"), mode !== "forgot");
  show($("forgot-link"), mode === "login");
  show($("auth-msg"), false);
  show($("resend-row"), false);
  $("switch-text").textContent = mode === "signup" ? "Already have an account?" : mode === "forgot" ? "Remembered it?" : "New here?";
  $("switch-mode").textContent = mode === "login" ? "Create an account" : "Log in";
  $("role-hint").textContent = mode === "forgot" ? "Choose the type of account you registered, then enter its email." : ROLE_HINT[selectedRole];
  document.querySelectorAll("#role-seg button").forEach(b => b.classList.toggle("active", b.dataset.role === selectedRole));
  $("a-pass").autocomplete = mode === "login" ? "current-password" : "new-password";
}
const roleLabel = (r) => (r === "organizer" ? "Event Creator" : "Attendee");

document.querySelectorAll("#role-seg button").forEach(b => b.addEventListener("click", () => { selectedRole = b.dataset.role; renderAuth(); }));
$("switch-mode").addEventListener("click", () => { mode = mode === "login" ? "signup" : "login"; renderAuth(); });
$("resend-link").addEventListener("click", run(async () => {
  const d = await api("/auth/resend-verification", { method: "POST", body: JSON.stringify({ email: $("a-email").value, role: selectedRole }) });
  notice(d.message, true);
}));
$("forgot-link").addEventListener("click", () => { mode = "forgot"; renderAuth(); });
["a-name", "a-email", "a-pass"].forEach(id => $(id).addEventListener("keydown", e => { if (e.key === "Enter") $("a-submit").click(); }));

$("a-submit").addEventListener("click", run(async () => {
  if (mode === "forgot") {
    const d = await api("/auth/forgot", { method: "POST", body: JSON.stringify({ email: $("a-email").value, role: selectedRole }) });
    $("auth-msg").textContent = d.message; show($("auth-msg"), true);
    return;
  }
  const payload = { email: $("a-email").value, password: $("a-pass").value, role: selectedRole };
  if (mode === "signup") payload.name = $("a-name").value;
  let data;
  try {
    data = await api(mode === "login" ? "/auth/login" : "/auth/register", { method: "POST", body: JSON.stringify(payload) });
  } catch (e) {
    if (e.code === "unverified") { notice(e.message, true); return; }
    throw e;
  }
  if (data.verification_required) {            // account created; must confirm email first
    mode = "login"; renderAuth();
    $("a-pass").value = "";
    notice(data.message, true);
    return;
  }
  token = data.token; user = data.user;
  localStorage.setItem("ems_token", token);
  $("a-pass").value = "";
  enterApp();
}));

function logout(expired = false) {
  token = null; user = null;
  localStorage.removeItem("ems_token");
  show($("org-app"), false); show($("att-app"), false); show($("who"), false); show($("auth"), true);
  if (expired) toast("Please log in again", true);
}
$("logout").addEventListener("click", () => logout());

function enterApp() {
  show($("auth"), false);
  show($("who"), true);
  $("who-name").textContent = user.name;
  $("who-role").textContent = roleLabel(user.role);
  show($("org-app"), user.role === "organizer");
  show($("att-app"), user.role === "attendee");
  if (user.role === "organizer") { switchTab("org", "o-events"); loadOrgEvents(); }
  else { switchTab("att", "a-browse"); loadBrowse(); }
}

function switchTab(app, tab) {
  const nav = $(app + "-nav"), root = $(app + "-app");
  nav.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  root.querySelectorAll("main > section").forEach(s => show(s, s.id === tab));
  show($("agenda-panel"), false);
}
$("org-nav").addEventListener("click", e => {
  const t = e.target.dataset.tab; if (!t) return;
  switchTab("org", t);
  if (t === "o-events") loadOrgEvents();
  if (t === "o-regs") loadOptions().then(loadRegs).catch(e => toast(e.message, true));
  if (t === "o-sched") loadOptions().then(loadSchedule).catch(e => toast(e.message, true));
});
$("att-nav").addEventListener("click", e => {
  const t = e.target.dataset.tab; if (!t) return;
  switchTab("att", t);
  if (t === "a-browse") loadBrowse();
  if (t === "a-mine") loadMine();
});

// ---------------- Shared bits ----------------
const fmtDate = (d) => d || "—";
const fmtDT = (d) => (d ? d.replace("T", " ") : "—");
function pager(m) {
  if (!m || m.pages <= 1) return "";
  return `<div class="pager"><button class="ghost" data-page="${m.page - 1}" ${m.page <= 1 ? "disabled" : ""}>&larr; Prev</button>
    <span>Page ${m.page} of ${m.pages} &middot; ${m.total} total</span>
    <button class="ghost" data-page="${m.page + 1}" ${m.page >= m.pages ? "disabled" : ""}>Next &rarr;</button></div>`;
}
function seatBar(ev) {
  const pct = ev.capacity ? Math.min(100, Math.round((ev.registered_count / ev.capacity) * 100)) : 0;
  return `<div>${ev.registered_count} / ${ev.capacity}</div><div class="bar ${ev.is_full ? "full" : ""}"><span style="width:${pct}%"></span></div>`;
}

// ================= ORGANIZER =================
async function loadOrgEvents() {
  const d = await api("/events?page=" + orgPage);
  orgPage = d.page; orgEvents = d.items;
  const box = $("ev-list");
  if (!d.total) box.innerHTML = '<div class="empty">You haven\'t created any events yet. Use the form above.</div>';
  else box.innerHTML = `<table><thead><tr><th>Event</th><th>Date</th><th>Location</th><th>Registered</th><th></th></tr></thead><tbody>` +
    orgEvents.map(e => `<tr>
      <td><b>${esc(e.name)}</b><div class="hint" style="margin:2px 0 0">${esc(e.description)}</div></td>
      <td>${esc(fmtDate(e.date))}</td><td>${esc(e.location || "—")}</td>
      <td>${seatBar(e)}${e.is_full ? '<span class="badge full">Full</span>' : `<span class="hint">${e.seats_left} left</span>`}</td>
      <td style="white-space:nowrap">
        <button class="ghost" data-act="regs" data-id="${e._id}">Registrations</button>
        <button class="ghost" data-act="edit" data-id="${e._id}">Edit</button>
        <button class="danger-link" data-act="del" data-id="${e._id}">Delete</button>
      </td></tr>`).join("") + "</tbody></table>" + pager(d);
  await loadOptions();
}
// dropdowns list ALL of the organizer's events, not just the current page
async function loadOptions() {
  const opts = await api("/events/options");
  for (const id of ["reg-event", "s-event"]) {
    const sel = $(id), prev = sel.value;
    sel.innerHTML = opts.map(e => `<option value="${e._id}">${esc(e.name)} (${esc(e.date)})</option>`).join("") || '<option value="">No events yet</option>';
    if (opts.some(e => e._id === prev)) sel.value = prev;
  }
}

function resetEventForm() {
  editingEventId = null;
  ["ev-name", "ev-date", "ev-loc", "ev-cap", "ev-desc"].forEach(i => $(i).value = "");
  $("ev-form-title").textContent = "Create an event";
  $("ev-save").textContent = "Create event";
  show($("ev-cancel"), false);
}
$("ev-cancel").addEventListener("click", resetEventForm);
$("ev-save").addEventListener("click", run(async () => {
  const payload = { name: $("ev-name").value, date: $("ev-date").value, location: $("ev-loc").value, capacity: $("ev-cap").value, description: $("ev-desc").value };
  if (editingEventId) await api("/events/" + editingEventId, { method: "PUT", body: JSON.stringify(payload) });
  else await api("/events", { method: "POST", body: JSON.stringify(payload) });
  toast(editingEventId ? "Event updated" : "Event created");
  resetEventForm();
  await loadOrgEvents();
}));

$("ev-list").addEventListener("click", run(async (e) => {
  const pg = e.target.closest("button[data-page]");
  if (pg) { orgPage = +pg.dataset.page; return loadOrgEvents(); }
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const ev = orgEvents.find(x => x._id === b.dataset.id);
  if (b.dataset.act === "edit") {
    editingEventId = ev._id;
    $("ev-name").value = ev.name; $("ev-date").value = ev.date; $("ev-loc").value = ev.location;
    $("ev-cap").value = ev.capacity; $("ev-desc").value = ev.description;
    $("ev-form-title").textContent = "Edit event";
    $("ev-save").textContent = "Save changes";
    show($("ev-cancel"), true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } else if (b.dataset.act === "del") {
    if (!confirm(`Delete "${ev.name}"? This also removes its ${ev.registered_count} registration(s) and schedule.`)) return;
    await api("/events/" + ev._id, { method: "DELETE" });
    toast("Event deleted");
    await loadOrgEvents();
  } else if (b.dataset.act === "regs") {
    switchTab("org", "o-regs");
    $("reg-event").value = ev._id;
    regPage = 1; $("reg-q").value = "";
    await loadRegs();
  }
}));

// ---- Registrations (tracking) ----
async function loadRegs() {
  const id = $("reg-event").value, box = $("reg-body");
  if (!id) { box.innerHTML = '<div class="empty">Create an event first.</div>'; return; }
  const q = $("reg-q").value.trim();
  const d = await api(`/events/${id}/attendees?page=${regPage}` + (q ? "&q=" + encodeURIComponent(q) : ""));
  regPage = d.page;
  const ev = d.event;
  box.innerHTML = `
    <div class="stats">
      <div class="stat"><b>${ev.registered_count} / ${ev.capacity}</b><span>Registered</span></div>
      <div class="stat"><b>${ev.seats_left}</b><span>Seats left</span></div>
      <div class="stat"><b>${d.checked_in_count}</b><span>Checked in</span></div>
    </div>` +
    (d.attendees.length ? `<table><thead><tr><th>Name</th><th>Email</th><th>Registered</th><th>Check-in</th><th></th></tr></thead><tbody>` +
      d.attendees.map(a => `<tr>
        <td>${esc(a.name)}</td><td>${esc(a.email)}</td><td>${esc((a.registered_at || "").slice(0, 10))}</td>
        <td><button class="ghost" data-act="toggle" data-id="${a._id}" data-in="${a.checked_in}">${a.checked_in ? "✓ Checked in" : "Check in"}</button></td>
        <td><button class="danger-link" data-act="remove" data-id="${a._id}">Remove</button></td></tr>`).join("") + "</tbody></table>" + pager(d)
      : `<div class="empty">${q ? "No attendees match your search." : "No one has registered yet."}</div>`);
}
$("reg-event").addEventListener("change", run(() => { regPage = 1; return loadRegs(); }));
let regTimer;
$("reg-q").addEventListener("input", () => { clearTimeout(regTimer); regTimer = setTimeout(run(() => { regPage = 1; return loadRegs(); }), 250); });
$("reg-body").addEventListener("click", run(async (e) => {
  const pg = e.target.closest("button[data-page]");
  if (pg) { regPage = +pg.dataset.page; return loadRegs(); }
  const b = e.target.closest("button[data-act]"); if (!b) return;
  if (b.dataset.act === "toggle") {
    await api("/attendees/" + b.dataset.id, { method: "PATCH", body: JSON.stringify({ checked_in: b.dataset.in !== "true" }) });
  } else if (b.dataset.act === "remove") {
    if (!confirm("Remove this attendee? Their seat will be freed.")) return;
    await api("/attendees/" + b.dataset.id, { method: "DELETE" });
    toast("Attendee removed");
  }
  await loadRegs();
}));

// ---- Schedule ----
async function loadSchedule() {
  const id = $("s-event").value, box = $("s-list");
  if (!id) { box.innerHTML = '<div class="empty">Create an event first.</div>'; return; }
  const list = await api("/sessions?event_id=" + id);
  box.innerHTML = list.length ? `<table><thead><tr><th>Start</th><th>End</th><th>Title</th><th>Speaker</th><th>Venue</th><th></th></tr></thead><tbody>` +
    list.map(s => `<tr><td>${esc(fmtDT(s.start_time))}</td><td>${esc(fmtDT(s.end_time))}</td><td><b>${esc(s.title)}</b></td><td>${esc(s.speaker || "—")}</td><td>${esc(s.venue || "—")}</td>
      <td><button class="danger-link" data-id="${s._id}">Delete</button></td></tr>`).join("") + "</tbody></table>"
    : '<div class="empty">No schedule items yet.</div>';
}
$("s-event").addEventListener("change", run(loadSchedule));
$("s-add").addEventListener("click", run(async () => {
  await api("/sessions", { method: "POST", body: JSON.stringify({
    event_id: $("s-event").value, title: $("s-title").value, speaker: $("s-speaker").value,
    start_time: $("s-start").value, end_time: $("s-end").value, venue: $("s-venue").value }) });
  ["s-title", "s-speaker", "s-start", "s-end", "s-venue"].forEach(i => $(i).value = "");
  toast("Added to schedule");
  await loadSchedule();
}));
$("s-list").addEventListener("click", run(async (e) => {
  const b = e.target.closest("button[data-id]"); if (!b) return;
  await api("/sessions/" + b.dataset.id, { method: "DELETE" });
  await loadSchedule();
}));

// ================= ATTENDEE =================
let browseEvents = [];
function eventAction(e) {
  if (e.is_registered) return `<button class="ghost" data-act="cancel" data-id="${e._id}">Cancel registration</button>`;
  if (e.is_full) return '<span class="badge full">Full</span>';
  return `<button class="primary" data-act="join" data-id="${e._id}">Register</button>`;
}
function attendeeTable(list, emptyMsg) {
  if (!list.length) return `<div class="empty">${emptyMsg}</div>`;
  return `<table><thead><tr><th>Event</th><th>Date</th><th>Location</th><th>Hosted by</th><th>Seats</th><th></th></tr></thead><tbody>` +
    list.map(e => `<tr><td><b>${esc(e.name)}</b><div class="hint" style="margin:2px 0 0">${esc(e.description)}</div></td>
      <td>${esc(fmtDate(e.date))}</td><td>${esc(e.location || "—")}</td><td>${esc(e.owner_name || "—")}</td>
      <td>${e.is_full ? '<span class="badge full">Full</span>' : `<span class="badge good">${e.seats_left} left</span>`}${e.is_registered ? ' <span class="badge">You\'re in</span>' : ""}</td>
      <td style="white-space:nowrap"><button class="ghost" data-act="agenda" data-id="${e._id}">Schedule</button> ${eventAction(e)}</td></tr>`).join("") + "</tbody></table>";
}
async function loadBrowse() {
  const q = $("b-search").value.trim();
  const d = await api(`/events?page=${browsePage}` + (q ? "&name=" + encodeURIComponent(q) : ""));
  browsePage = d.page; browseEvents = d.items;
  $("b-list").innerHTML = attendeeTable(browseEvents, "No events available right now.") + pager(d);
}
async function loadMine() {
  const list = await api("/my/registrations");
  browseEvents = list;
  $("m-list").innerHTML = attendeeTable(list, "You haven't registered for any events yet.");
}
let searchTimer;
$("b-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(run(() => { browsePage = 1; return loadBrowse(); }), 250); });

async function attendeeClick(e, reload) {
  const pg = e.target.closest("button[data-page]");
  if (pg) { browsePage = +pg.dataset.page; return reload(); }
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const id = b.dataset.id;
  if (b.dataset.act === "join") { await api(`/events/${id}/register`, { method: "POST" }); toast("You're registered!"); }
  else if (b.dataset.act === "cancel") {
    if (!confirm("Cancel your registration?")) return;
    await api(`/events/${id}/register`, { method: "DELETE" }); toast("Registration cancelled");
  } else if (b.dataset.act === "agenda") {
    const ev = browseEvents.find(x => x._id === id);
    const list = await api("/sessions?event_id=" + id);
    $("agenda-title").textContent = "Schedule — " + ev.name;
    $("agenda-list").innerHTML = list.length ? `<table><thead><tr><th>Start</th><th>End</th><th>Title</th><th>Speaker</th><th>Venue</th></tr></thead><tbody>` +
      list.map(s => `<tr><td>${esc(fmtDT(s.start_time))}</td><td>${esc(fmtDT(s.end_time))}</td><td><b>${esc(s.title)}</b></td><td>${esc(s.speaker || "—")}</td><td>${esc(s.venue || "—")}</td></tr>`).join("") + "</tbody></table>"
      : '<div class="empty">The organizer hasn\'t published a schedule yet.</div>';
    show($("agenda-panel"), true);
    $("agenda-panel").scrollIntoView({ behavior: "smooth" });
    return;
  }
  await reload();
}
$("b-list").addEventListener("click", run(e => attendeeClick(e, loadBrowse)));
$("m-list").addEventListener("click", run(e => attendeeClick(e, loadMine)));

// ---------------- Password reset (opened from the emailed link) ----------------
function showReset() {
  show($("auth"), false); show($("org-app"), false); show($("att-app"), false); show($("who"), false);
  show($("reset-panel"), true);
}
function leaveReset() {
  resetToken = null;
  history.replaceState(null, "", location.pathname);
  show($("reset-panel"), false); show($("auth"), true);
  mode = "login"; renderAuth();
}
$("r-back").addEventListener("click", leaveReset);
$("r-submit").addEventListener("click", run(async () => {
  if ($("r-pass").value !== $("r-pass2").value) throw new Error("The two passwords don't match");
  await api("/auth/reset", { method: "POST", body: JSON.stringify({ token: resetToken, password: $("r-pass").value }) });
  $("r-pass").value = $("r-pass2").value = "";
  leaveReset();
  toast("Password updated. Please log in.");
}));

// ---------------- Boot ----------------
(async function boot() {
  renderAuth();
  if (location.hash.startsWith("#verify=")) {
    const t = location.hash.slice(8);
    token = null; localStorage.removeItem("ems_token");
    history.replaceState(null, "", location.pathname);
    try {
      const d = await api("/auth/verify", { method: "POST", body: JSON.stringify({ token: t }) });
      selectedRole = d.role; mode = "login"; renderAuth();
      notice(d.message);
    } catch (e) { notice(e.message); }
    return;
  }
  if (location.hash.startsWith("#reset=")) {
    resetToken = location.hash.slice(7);
    token = null; localStorage.removeItem("ems_token");
    showReset();
    return;
  }
  if (!token) return;
  try { user = await api("/auth/me"); enterApp(); }
  catch { logout(); }
})();
