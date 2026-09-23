const state = {
  me: null,
  csrf: "",
  expiresAt: 0,
  employees: [],
  employeeId: null,
  view: "employee",
  profile: null,
  selectedFiles: [],
  expiryTimer: null,
  authCheckPending: false,
};

const authChannel = "BroadcastChannel" in window ? new BroadcastChannel("career-quest-auth") : null;
const AUTH_EVENT_KEY = "career-quest-auth-event";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const els = {
  authScreen: $("#authScreen"),
  appShell: $("#appShell"),
  loginForm: $("#loginForm"),
  loginUsername: $("#loginUsername"),
  loginPassword: $("#loginPassword"),
  loginButton: $("#loginButton"),
  loginError: $("#loginError"),
  loading: $("#loadingState"),
  employeeView: $("#employeeView"),
  hrView: $("#hrView"),
  securityView: $("#securityView"),
  picker: $("#employeePicker"),
  pickerWrap: $("#employeePickerWrap"),
  dataSource: $("#dataSource"),
  pageTitle: $("#pageTitle"),
  modalBackdrop: $("#modalBackdrop"),
  uploadModal: $("#uploadModal"),
  explanationModal: $("#explanationModal"),
  accountModal: $("#accountModal"),
  fileInput: $("#fileInput"),
  selectedFiles: $("#selectedFiles"),
  uploadButton: $("#uploadButton"),
  toast: $("#toast"),
};

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function api(path, config = {}) {
  const { skipAuthRedirect = false, ...options } = config;
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (!["GET", "HEAD"].includes(method)) {
    headers["Content-Type"] = "application/json";
    if (state.csrf && path !== "/api/login") headers["X-CSRF-Token"] = state.csrf;
  }
  let response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...options, headers });
  } catch (_error) {
    throw new ApiError("Сервер недоступен. Запустите python3 server.py", 0, "NETWORK_ERROR");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login" && !skipAuthRedirect) {
      showLogin("Сессия завершена. Войдите снова.");
    }
    throw new ApiError(data.error || `Ошибка ${response.status}`, response.status, data.code);
  }
  return data;
}

function initials(name = "") {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "CQ";
}

function plural(count, one, few, many) {
  const value = Math.abs(count) % 100;
  const last = value % 10;
  if (value > 10 && value < 20) return many;
  if (last > 1 && last < 5) return few;
  if (last === 1) return one;
  return many;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function isHr() {
  return state.me?.role === "hr";
}

function roleLabel(role) {
  return role === "hr" ? "HR Business Partner" : "Сотрудник";
}

function showLogin(message = "") {
  clearTimeout(state.expiryTimer);
  state.expiryTimer = null;
  state.me = null;
  state.csrf = "";
  state.expiresAt = 0;
  state.employees = [];
  state.employeeId = null;
  state.profile = null;
  state.selectedFiles = [];
  state.authCheckPending = false;
  state.view = "employee";
  ["#recommendationGrid", "#skillsList", "#historyList", "#kpiGrid", "#gapChart", "#participationChart", "#watchTable", "#careerTracks", "#retentionCases", "#securityKpis", "#securityControls", "#auditTable", "#selectedFiles"].forEach((selector) => {
    const element = $(selector);
    if (element) element.innerHTML = "";
  });
  els.picker.innerHTML = "";
  els.fileInput.value = "";
  els.uploadButton.disabled = true;
  els.employeeView.hidden = true;
  els.hrView.hidden = true;
  els.securityView.hidden = true;
  els.loading.hidden = true;
  $("#sidebar").classList.remove("open");
  $("#accountName").textContent = "Пользователь";
  $("#roleBadge").textContent = "Сессия не активна";
  $("#sessionUserName").textContent = "Пользователь";
  $("#sessionAvatar").textContent = "CQ";
  $("#sessionRole").textContent = "—";
  $("#sessionExpires").textContent = "—";
  $("#sessionRights").textContent = "—";
  $("#employeeAvatar").textContent = "CQ";
  $("#employeeName").textContent = "—";
  $("#employeeRole").textContent = "—";
  $("#employeeTenure").textContent = "—";
  $("#currentGrade").textContent = "—";
  $("#targetGrade").textContent = "—";
  $("#skillsTargetGrade").textContent = "—";
  $("#readinessValue").textContent = "—";
  $("#readinessLabel").textContent = "—";
  $("#decisionInsightText").textContent = "";
  $("#retentionCount").textContent = "—";
  $("#explanationTitle").textContent = "Почему этот шаг";
  $("#explanationScore").textContent = "0";
  $("#factorList").innerHTML = "";
  $("#scoreBreakdown").innerHTML = "";
  els.dataSource.textContent = "Сессия не активна";
  closeModals();
  els.appShell.hidden = true;
  els.authScreen.hidden = false;
  els.loginError.hidden = !message;
  els.loginError.textContent = message;
  els.loginPassword.value = "";
  setTimeout(() => els.loginUsername.focus(), 30);
}

async function enterApp(user) {
  state.csrf = user.csrf_token || "";
  state.expiresAt = Date.now() + Number(user.expires_in || 0) * 1000;
  state.me = { ...user };
  delete state.me.csrf_token;
  els.appShell.hidden = true;
  configureRoleUi();
  await loadEmployees(user.employee_id || "E0028");
  if (isHr()) {
    state.view = "hr";
    setActiveNav();
    showLoading();
    const data = await api("/api/hr");
    els.dataSource.textContent = data.data_source;
    renderHr(data);
    els.loading.hidden = true;
    els.hrView.hidden = false;
  } else {
    state.view = "employee";
    state.employeeId = state.me.employee_id;
    setActiveNav();
    await loadEmployee(state.employeeId);
  }
  scheduleSessionExpiry();
  els.authScreen.hidden = true;
  els.appShell.hidden = false;
}

function scheduleSessionExpiry() {
  clearTimeout(state.expiryTimer);
  const remaining = state.expiresAt - Date.now();
  if (remaining <= 0) return showLogin("Сессия завершена. Войдите снова.");
  const delay = Math.min(remaining + 100, 2_147_000_000);
  state.expiryTimer = setTimeout(() => {
    if (Date.now() >= state.expiresAt) showLogin("Сессия завершена по тайм-ауту. Войдите снова.");
    else scheduleSessionExpiry();
  }, delay);
}

function enforceSessionExpiry() {
  if (state.me && Date.now() >= state.expiresAt) showLogin("Сессия завершена по тайм-ауту. Войдите снова.");
}

function announceAuthChange(type) {
  const message = { type, username: state.me?.username || "", nonce: `${Date.now()}-${Math.random()}` };
  authChannel?.postMessage(message);
  try { localStorage.setItem(AUTH_EVENT_KEY, JSON.stringify(message)); } catch (_error) { /* optional fallback */ }
}

function handleExternalAuthChange(message) {
  // The HttpOnly cookie is shared by every tab on this origin, so any login
  // or logout changes the effective browser session for every open view.
  if (state.me && ["login", "logout"].includes(message?.type)) {
    showLogin("Сессия изменена в другой вкладке. Войдите снова.");
  }
}

async function revalidateSession() {
  if (!state.me || state.authCheckPending) return;
  if (Date.now() >= state.expiresAt) {
    showLogin("Сессия завершена по тайм-ауту. Войдите снова.");
    return;
  }
  state.authCheckPending = true;
  try {
    const result = await api("/api/me", { skipAuthRedirect: true });
    if (!state.me) return;
    if (result.user.username !== state.me.username || result.user.role !== state.me.role) {
      showLogin("Сессия была заменена. Войдите снова.");
      return;
    }
    state.csrf = result.user.csrf_token || state.csrf;
    state.expiresAt = Date.now() + Number(result.user.expires_in || 0) * 1000;
    scheduleSessionExpiry();
  } catch (error) {
    if (error.status === 401 || error.status === 403) showLogin("Сессия отозвана. Войдите снова.");
  } finally {
    state.authCheckPending = false;
  }
}

function configureRoleUi() {
  const hr = isHr();
  $$(".role-hr").forEach((element) => { element.hidden = !hr; });
  $("#employeeNavLabel").textContent = hr ? "Профили сотрудников" : "Мой путь";
  $("#privacyCopy").textContent = hr
    ? "Детальные данные доступны только роли HR и журналируются."
    : "Профиль доступен только его владельцу.";
  $("#accountName").textContent = state.me.display_name;
  $("#roleBadge").textContent = roleLabel(state.me.role);
  $("#accountAvatar").textContent = initials(state.me.display_name);
  $("#sessionUserName").textContent = state.me.display_name;
  $("#sessionAvatar").textContent = initials(state.me.display_name);
  $("#sessionRole").textContent = roleLabel(state.me.role);
  $("#sessionRights").textContent = hr
    ? "HR-аналитика · профили · датасет"
    : "Только свой профиль и активности";
}

async function loadEmployees(preferredId = state.employeeId) {
  const result = await api("/api/employees");
  state.employees = result.employees;
  els.dataSource.textContent = result.data_source;
  els.picker.innerHTML = state.employees
    .map((employee) => `<option value="${escapeHtml(employee.employee_id)}">${escapeHtml(employee.employee_id)} · ${escapeHtml(employee.name)} · ${escapeHtml(employee.role_label)} · ${escapeHtml(employee.grade)}</option>`)
    .join("");
  const ownId = state.me?.employee_id;
  state.employeeId = state.employees.some((item) => item.employee_id === preferredId)
    ? preferredId
    : (ownId || state.employees[0]?.employee_id || null);
  els.picker.value = state.employeeId || "";
}

async function loadEmployee(employeeId = state.employeeId) {
  const allowedId = isHr() ? employeeId : state.me.employee_id;
  state.employeeId = allowedId;
  showLoading();
  const profile = await api(`/api/employee?id=${encodeURIComponent(allowedId)}`);
  state.profile = profile;
  renderEmployee(profile);
  els.loading.hidden = true;
  els.employeeView.hidden = false;
}

function showLoading() {
  els.employeeView.hidden = true;
  els.hrView.hidden = true;
  els.securityView.hidden = true;
  els.loading.hidden = false;
}

function renderEmployee(profile) {
  const employee = profile.employee;
  const openGaps = profile.skills.filter((skill) => skill.gap > 0).length;
  $("#viewingBanner").hidden = !isHr();
  $("#employeeAvatar").textContent = initials(employee.name);
  $("#employeeName").textContent = employee.name || employee.employee_id;
  $("#employeeRole").textContent = profile.role_label || employee.role;
  $("#employeeTenure").textContent = `${employee.tenure_months || 0} ${plural(employee.tenure_months || 0, "месяц", "месяца", "месяцев")} в компании`;
  $("#currentGrade").textContent = employee.grade;
  $("#targetGrade").textContent = profile.target_grade;
  $("#skillsTargetGrade").textContent = profile.target_grade;
  $("#readinessValue").textContent = `${Math.round(profile.readiness)}%`;
  $("#readinessRing").style.background = `conic-gradient(var(--lime) 0deg, var(--lime) ${profile.readiness * 3.6}deg, rgba(255,255,255,.12) ${profile.readiness * 3.6}deg)`;
  $("#trajectoryLine").style.width = `${profile.readiness}%`;
  $("#readinessLabel").textContent = profile.terminal_grade
    ? "Максимальный грейд · нужен экспертный трек"
    : openGaps
      ? `Нужно закрыть ${openGaps} ${plural(openGaps, "разрыв", "разрыва", "разрывов")}`
      : "Требования грейда закрыты";
  els.dataSource.textContent = profile.data_source;
  const insight = $("#decisionInsight");
  insight.hidden = !profile.decision_insight;
  $("#decisionInsightText").textContent = profile.decision_insight || "";
  renderRecommendations(profile.recommendations, profile.terminal_grade);
  renderSkills(profile.skills);
  renderHistory(profile.history);
}

function renderRecommendations(recommendations, terminalGrade = false) {
  const container = $("#recommendationGrid");
  if (!recommendations.length) {
    container.innerHTML = terminalGrade
      ? `<div class="panel"><strong>Грейд Lead достигнут</strong><p>Следующая цель — экспертный, архитектурный или управленческий трек, который сотрудник выбирает вместе с HR.</p></div>`
      : `<div class="panel"><strong>Маршрут завершён</strong><p>Текущие навыки уже соответствуют следующему грейду. HR может добавить новую цель.</p></div>`;
    return;
  }
  container.innerHTML = recommendations
    .map((item, index) => {
      const skills = item.affected_skills.map((skill) => `${escapeHtml(skill.name)} ${Number(skill.before)}→${Number(skill.after)}`).join(" · ");
      const action = isHr()
        ? `<button class="readonly-button" disabled>Только сотрудник</button>`
        : `<button class="complete-button" data-complete="${escapeHtml(item.event_id)}">Отметить выполненным</button>`;
      return `
        <article class="recommendation-card ${index === 0 ? "primary" : ""}">
          <div class="rank-row"><span class="rank">${index + 1}</span><span class="score"><b>${Math.round(item.score)}</b> / 100</span></div>
          <h3>${escapeHtml(item.title)}</h3>
          <div class="recommendation-meta"><span>${escapeHtml(typeLabel(item.type))}</span><span>≈ ${Number(item.duration_hours)} ч.</span><span>${skills}</span></div>
          <div class="impact-box"><span>Готовность к грейду</span><strong>${Number(item.current_readiness)}% → ${Number(item.projected_readiness)}%</strong></div>
          <div class="card-actions">
            <button data-details="${index}">Почему этот шаг</button>${action}
          </div>
        </article>`;
    })
    .join("");
  $$('[data-details]').forEach((button) => button.addEventListener("click", () => openExplanation(recommendations[Number(button.dataset.details)])));
  $$('[data-complete]').forEach((button) => button.addEventListener("click", () => completeActivity(button.dataset.complete, button)));
}

function typeLabel(type) {
  return ({ workshop: "Практикум", mentoring: "Менторство", course: "Курс", project: "Проект", assessment: "Аттестация", rotation: "Ротация" })[type] || "Активность";
}

function renderSkills(skills) {
  $("#skillsList").innerHTML = skills
    .map((skill) => `
      <div class="skill-row">
        <div class="skill-name"><strong>${escapeHtml(skill.name)}</strong><small>${skill.type === "hard" ? "hard skill" : "soft skill"}</small></div>
        <div class="skill-track" aria-label="${escapeHtml(skill.name)}: ${Number(skill.current)} из ${Number(skill.required)}">
          <i class="skill-fill" style="width:${Number(skill.current) / 5 * 100}%"></i>
          <i class="target-marker" style="left:${Number(skill.required) / 5 * 100}%"></i>
        </div>
        <div class="skill-level"><b>${Number(skill.current)}</b> / ${Number(skill.required)}${skill.gap ? ` · −${Number(skill.gap)}` : " · ✓"}</div>
      </div>`)
    .join("");
}

function renderHistory(history) {
  $("#historyCount").textContent = `${history.length} ${plural(history.length, "событие", "события", "событий")}`;
  $("#historyList").innerHTML = history.length
    ? history.map((item) => {
        const completed = item.status === "completed";
        const label = completed ? (item.on_time ? "Выполнено вовремя" : "Выполнено") : item.status === "declined" ? "Отказ" : "Пропущено";
        return `<div class="history-item"><span class="history-status ${escapeHtml(item.status)}">${completed ? "✓" : "×"}</span><div><strong>${escapeHtml(item.title)}</strong><small>${label}</small></div><span class="history-date">${escapeHtml(item.date || "")}</span></div>`;
      }).join("")
    : `<p class="empty-note">История участия пока пуста.</p>`;
}

async function completeActivity(eventId, button) {
  if (isHr()) return;
  button.disabled = true;
  button.textContent = "Обновляю прогресс…";
  try {
    const updated = await api("/api/complete", { method: "POST", body: JSON.stringify({ event_id: eventId }) });
    burstConfetti(button.getBoundingClientRect());
    state.profile = updated;
    renderEmployee(updated);
    showToast("Активность учтена · траектория пересчитана");
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
    button.textContent = "Отметить выполненным";
  }
}

function openExplanation(item) {
  $("#explanationTitle").textContent = item.title;
  $("#explanationScore").textContent = Math.round(item.score);
  $("#factorList").innerHTML = item.factors.map((factor, index) => `<div class="factor-item"><span>${index + 1}</span><div><strong>${escapeHtml(factor.label)}</strong><p>${escapeHtml(factor.text)}</p></div></div>`).join("");
  const labels = { grade_relevance: "Грейд и gap", trajectory_impact: "Влияние", history_fit: "История", feasibility: "Выполнимость" };
  const weights = { grade_relevance: 46, trajectory_impact: 26, history_fit: 18, feasibility: 10 };
  $("#scoreBreakdown").innerHTML = Object.entries(item.score_breakdown).map(([key, value]) => {
    const contribution = (Number(value) * Number(weights[key] || 0) / 100).toFixed(1);
    return `<div class="score-part"><b>${Number(value)}% × ${Number(weights[key] || 0)}%</b><small>${escapeHtml(labels[key] || key)} · +${contribution}</small></div>`;
  }).join("");
  openModal(els.explanationModal);
}

async function showHr() {
  if (!isHr()) return showToast("Раздел доступен только HR", true);
  state.view = "hr";
  setActiveNav();
  showLoading();
  try {
    const data = await api("/api/hr");
    els.dataSource.textContent = data.data_source;
    renderHr(data);
    els.loading.hidden = true;
    els.hrView.hidden = false;
  } catch (error) {
    els.loading.hidden = true;
    showToast(error.message, true);
  }
}

function renderHr(data) {
  const cards = [
    [data.employees, "Сотрудников в выборке", "Покрытие профилей"],
    [`${data.average_readiness}%`, "Средняя готовность", "К следующему грейду"],
    [`${data.completion_rate}%`, "Завершаемость", "По всем активностям"],
    [data.without_step, "Без следующего шага", "Нужно действие HR"],
  ];
  $("#kpiGrid").innerHTML = cards.map(([value, label, note]) => `<article class="kpi-card"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong><span>${escapeHtml(note)}</span></article>`).join("");
  const maxGap = Math.max(1, ...data.skill_gaps.map((item) => Number(item.total_gap)));
  $("#gapChart").innerHTML = data.skill_gaps.map((item) => `<div class="bar-row"><label>${escapeHtml(item.name)}</label><div class="bar-track"><i style="width:${Number(item.total_gap) / maxGap * 100}%"></i></div><b>${Number(item.total_gap)}</b></div>`).join("");
  const statusLabels = { completed: "Завершено", skipped: "Пропущено", declined: "Отказ" };
  const colors = { completed: "#168457", skipped: "#ef9854", declined: "#d9675d" };
  $("#participationChart").innerHTML = data.participation.map((item) => `<div class="participation-row"><i style="background:${colors[item.status] || "#87918b"}"></i><span>${escapeHtml(statusLabels[item.status] || item.status)}</span><strong>${Number(item.percent)}%</strong></div>`).join("");
  $("#watchTable").innerHTML = `<div class="watch-row header"><span>Сотрудник</span><span>Роль</span><span>Готовность</span><span>Участие</span></div>` + data.watchlist.map((item) => `<div class="watch-row"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.role)}</span><b>${Number(item.readiness)}%</b><span>${Number(item.participation)}%</span></div>`).join("");

  const tracks = Array.isArray(data.career_tracks) ? data.career_tracks : [];
  $("#careerTracks").innerHTML = tracks.length
    ? tracks.map((track) => {
        const steps = Array.isArray(track.path) ? track.path : [];
        return `<article class="career-track"><div><strong>${escapeHtml(track.label)}</strong><span>гипотеза пилота</span></div><div class="track-steps">${steps.map((step, index) => `${index ? "<b>→</b>" : ""}<span>${escapeHtml(step)}</span>`).join("")}</div></article>`;
      }).join("")
    : `<p class="empty-note">Карьерные графы ещё не загружены.</p>`;

  const retention = data.retention_summary || {};
  $("#retentionCount").textContent = `${Number(retention.high_risk_count || 0)} high · ${Number(retention.medium || 0)} medium`;
  const retentionCases = Array.isArray(data.retention_cases) ? data.retention_cases.slice(0, 4) : [];
  $("#retentionCases").innerHTML = retentionCases.length
    ? retentionCases.map((item) => {
        const evidence = (item.signals || []).slice(0, 2).map((signal) => escapeHtml(signal.evidence)).join(" ");
        const band = item.risk_band === "high" ? "HIGH" : item.risk_band === "medium" ? "MED" : "LOW";
        return `<article class="retention-case"><span class="risk-dot">${band}</span><div><strong>${escapeHtml(item.name)} · ${escapeHtml(item.role)}</strong><p>${evidence || "Нет срочных сигналов по выбранным факторам."}</p><small>Следующий шаг: ${escapeHtml(item.suggested_next_action)}</small></div></article>`;
      }).join("")
    : `<p class="empty-note">Объяснимых сигналов пока нет.</p>`;
}

async function showSecurity() {
  if (!isHr()) return showToast("Security Center доступен только HR", true);
  state.view = "security";
  setActiveNav();
  showLoading();
  try {
    const data = await api("/api/security");
    renderSecurity(data);
    els.loading.hidden = true;
    els.securityView.hidden = false;
  } catch (error) {
    els.loading.hidden = true;
    showToast(error.message, true);
  }
}

function renderSecurity(data) {
  const kpis = [
    [data.controls.length, "Активных контролей", "defence in depth"],
    [data.active_sessions, "Активных сессий", "server-side state"],
    [`${data.session_ttl_minutes} мин`, "TTL сессии", "автозавершение"],
    [data.demo_mode ? "DEMO" : "CUSTOM", "Local auth adapter", data.demo_mode ? "synthetic data" : "quick login off"],
  ];
  $("#securityKpis").innerHTML = kpis.map(([value, label, note]) => `<article><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong><span>${escapeHtml(note)}</span></article>`).join("");
  $("#securityControls").innerHTML = data.controls.map((control) => `<article><span class="control-check">✓</span><div><strong>${escapeHtml(control.name)}</strong><p>${escapeHtml(control.detail)}</p></div><small>${escapeHtml(control.status)}</small></article>`).join("");
  $("#auditTable").innerHTML = data.audit_events.length
    ? `<div class="audit-row audit-header"><span>Время UTC</span><span>Событие</span><span>Actor</span><span>Результат</span></div>` + data.audit_events.map((event) => `<div class="audit-row"><span>${escapeHtml(event.timestamp.replace("T", " ").replace("+00:00", ""))}</span><span><strong>${escapeHtml(event.action)}</strong><small>${escapeHtml(event.detail)}</small></span><span>${escapeHtml(event.actor)} · ${escapeHtml(event.role)}</span><b class="audit-${escapeHtml(event.outcome)}">${escapeHtml(event.outcome)}</b></div>`).join("")
    : `<p class="empty-note">Событий пока нет.</p>`;
}

async function showEmployeeView() {
  state.view = "employee";
  setActiveNav();
  if (!isHr()) state.employeeId = state.me.employee_id;
  try {
    await loadEmployee(state.employeeId);
  } catch (error) {
    els.loading.hidden = true;
    showToast(error.message, true);
  }
}

function setActiveNav() {
  $("#employeeNav").classList.toggle("active", state.view === "employee");
  $("#hrNav").classList.toggle("active", state.view === "hr");
  $("#securityNav").classList.toggle("active", state.view === "security");
  els.pickerWrap.hidden = !(isHr() && state.view === "employee");
  const titles = { employee: isHr() ? "Профиль сотрудника" : "Карьерная траектория", hr: "HR-аналитика", security: "Security Center" };
  els.pageTitle.textContent = titles[state.view];
  $("#sidebar").classList.remove("open");
}

function openModal(modal) {
  closeModals();
  els.modalBackdrop.hidden = false;
  modal.hidden = false;
  els.modalBackdrop.classList.add("open");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeModals() {
  els.modalBackdrop.classList.remove("open");
  els.modalBackdrop.hidden = true;
  $$(".modal").forEach((modal) => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    modal.hidden = true;
  });
}

function openAccount() {
  const minutes = Math.max(0, Math.ceil((state.expiresAt - Date.now()) / 60000));
  $("#sessionExpires").textContent = `${minutes} ${plural(minutes, "минута", "минуты", "минут")}`;
  openModal(els.accountModal);
}

function selectFiles(files) {
  state.selectedFiles = [...files];
  els.uploadButton.disabled = !state.selectedFiles.length;
  els.selectedFiles.innerHTML = state.selectedFiles.map((file) => `<div class="file-chip"><span>${escapeHtml(file.name)}</span><b>${Math.ceil(file.size / 1024)} КБ</b></div>`).join("");
}

async function uploadFiles() {
  if (!isHr()) return;
  els.uploadButton.disabled = true;
  els.uploadButton.textContent = "Проверяю схему…";
  try {
    if (state.selectedFiles.length > 8) throw new Error("Можно загрузить не более 8 файлов");
    const files = [];
    let totalSize = 0;
    for (const file of state.selectedFiles) {
      if (file.size > 4_000_000) throw new Error(`${file.name}: файл превышает 4 МБ`);
      totalSize += file.size;
      if (totalSize > 6_000_000) throw new Error("Суммарный размер файлов превышает 6 МБ");
      const name = file.name.toLowerCase();
      if (!name.endsWith(".json") && !name.endsWith(".csv")) throw new Error(`${file.name}: поддерживаются только JSON и CSV`);
      files.push({ name: file.name, content: await file.text() });
    }
    const rawPayload = JSON.stringify({ files });
    if (new TextEncoder().encode(rawPayload).length > 7_500_000) throw new Error("Запрос с файлами слишком велик");
    const result = await api("/api/upload", { method: "POST", body: rawPayload });
    state.selectedFiles = [];
    els.fileInput.value = "";
    els.selectedFiles.innerHTML = "";
    closeModals();
    showToast(`Схема валидна · в наборе ${result.employees} профилей`);
    await loadEmployees(state.employeeId);
    await showHr();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    els.uploadButton.disabled = !state.selectedFiles.length;
    els.uploadButton.textContent = "Проверить и загрузить";
  }
}

function showToast(message, error = false) {
  els.toast.textContent = message;
  els.toast.classList.toggle("error", error);
  els.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 3400);
}

function burstConfetti(rect) {
  const colors = ["#c9f36a", "#0b6640", "#7356e8", "#ef9854"];
  for (let index = 0; index < 24; index += 1) {
    const particle = document.createElement("span");
    particle.className = "confetti";
    particle.style.left = `${rect.left + rect.width / 2}px`;
    particle.style.top = `${rect.top}px`;
    particle.style.background = colors[index % colors.length];
    particle.style.setProperty("--x", `${Math.random() * 180 - 90}px`);
    document.body.appendChild(particle);
    setTimeout(() => particle.remove(), 950);
  }
}

els.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.loginError.hidden = true;
  els.loginButton.disabled = true;
  els.loginButton.innerHTML = "Проверяю доступ…";
  try {
    const result = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: els.loginUsername.value, password: els.loginPassword.value }),
      skipAuthRedirect: true,
    });
    els.loginPassword.value = "";
    await enterApp(result.user);
    announceAuthChange("login");
  } catch (error) {
    if (state.me) {
      try {
        await api("/api/logout", { method: "POST", body: "{}", skipAuthRedirect: true });
      } catch (_logoutError) {
        // A new login rotates any session that could not be revoked here.
      }
      showLogin("");
    }
    els.loginError.textContent = error.message;
    els.loginError.hidden = false;
  } finally {
    els.loginButton.disabled = false;
    els.loginButton.innerHTML = "Войти в Career Quest <span>→</span>";
  }
});

$$('[data-demo-login]').forEach((button) => button.addEventListener("click", () => {
  els.loginUsername.value = button.dataset.demoLogin;
  els.loginPassword.value = button.dataset.demoPassword;
  els.loginForm.requestSubmit();
}));

$("#employeeNav").addEventListener("click", showEmployeeView);
$("#hrNav").addEventListener("click", showHr);
$("#securityNav").addEventListener("click", showSecurity);
els.picker.addEventListener("change", (event) => loadEmployee(event.target.value).catch((error) => showToast(error.message, true)));
$("#mobileMenu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$("#openUploadButton").addEventListener("click", () => { if (isHr()) openModal(els.uploadModal); });
$("#accountButton").addEventListener("click", openAccount);
els.modalBackdrop.addEventListener("click", closeModals);
$$('[data-close-modal]').forEach((button) => button.addEventListener("click", closeModals));
els.fileInput.addEventListener("change", (event) => selectFiles(event.target.files));
els.uploadButton.addEventListener("click", uploadFiles);
$("#dropZone").addEventListener("dragover", (event) => { event.preventDefault(); event.currentTarget.classList.add("dragging"); });
$("#dropZone").addEventListener("dragleave", (event) => event.currentTarget.classList.remove("dragging"));
$("#dropZone").addEventListener("drop", (event) => { event.preventDefault(); event.currentTarget.classList.remove("dragging"); selectFiles(event.dataTransfer.files); });

$("#resetButton").addEventListener("click", async () => {
  if (!isHr() || !window.confirm("Восстановить встроенный синтетический набор?")) return;
  try {
    await api("/api/reset", { method: "POST", body: "{}" });
    state.employeeId = "E0028";
    await loadEmployees(state.employeeId);
    await showHr();
    showToast("Демо-данные восстановлены");
  } catch (error) {
    showToast(error.message, true);
  }
});

$("#logoutButton").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
    announceAuthChange("logout");
    showLogin("Сессия безопасно завершена.");
  } catch (error) {
    if (error.status === 401 || !state.me) {
      showLogin("Сессия уже завершена. Войдите снова.");
      return;
    }
    closeModals();
    showToast("Не удалось подтвердить выход на сервере. Повторите попытку.", true);
  }
});

document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModals(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) revalidateSession(); });
window.addEventListener("focus", revalidateSession);
authChannel?.addEventListener("message", (event) => handleExternalAuthChange(event.data));
window.addEventListener("storage", (event) => {
  if (event.key !== AUTH_EVENT_KEY || !event.newValue) return;
  try { handleExternalAuthChange(JSON.parse(event.newValue)); } catch (_error) { /* ignore malformed local events */ }
});

(async function init() {
  try {
    const config = await api("/api/config", { skipAuthRedirect: true });
    $("#demoAccess").hidden = !config.demo_quick_login;
    if (!config.demo_mode) {
      $(".auth-footnote").textContent = "Custom local auth: quick login отключён, используйте credentials из secret environment. OIDC/SSO — production roadmap.";
    } else if (!config.demo_quick_login) {
      $(".auth-footnote").textContent = "Demo mode с пользовательскими паролями: введите credentials из переменных окружения.";
    }
    const result = await api("/api/me", { skipAuthRedirect: true });
    await enterApp(result.user);
  } catch (error) {
    showLogin(error.status === 0 ? error.message : "");
  }
})();
