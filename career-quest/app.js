const state = {
  user: null,
  csrf: null,
  plannerRequest: 0,
  selectedSteps: [],
  employees: [],
  employeeId: "E0028",
  view: "employee",
  profile: null,
  selectedFiles: [],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const els = {
  loading: $("#loadingState"),
  employeeView: $("#employeeView"),
  hrView: $("#hrView"),
  picker: $("#employeePicker"),
  pickerWrap: $("#employeePickerWrap"),
  dataSource: $("#dataSource"),
  pageTitle: $("#pageTitle"),
  modalBackdrop: $("#modalBackdrop"),
  uploadModal: $("#uploadModal"),
  explanationModal: $("#explanationModal"),
  fileInput: $("#fileInput"),
  selectedFiles: $("#selectedFiles"),
  uploadButton: $("#uploadButton"),
  toast: $("#toast"),
};

async function api(path, options = {}) {
  const requestUser = state.user;
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf || "", ...(options.headers || {}) },
  });
  if (!response.headers.get("Content-Type")?.includes("application/json")) {
    throw new Error("Сервер вернул страницу вместо API. Перезапустите server.py из папки career-quest и откройте http://127.0.0.1:8000.");
  }
  const data = await response.json();
  if (requestUser !== state.user) throw new Error("Сессия изменилась. Повторите действие.");
  if (response.status === 401 && path !== "/api/login") showLogin();
  if (!response.ok) throw new Error(data.error || `Ошибка ${response.status}`);
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

async function loadEmployees(preferredId = state.employeeId) {
  const result = await api("/api/employees");
  state.employees = result.employees;
  els.dataSource.textContent = result.data_source;
  els.picker.innerHTML = state.employees
    .map((employee) => `<option value="${escapeHtml(employee.employee_id)}">${escapeHtml(employee.employee_id)} · ${escapeHtml(employee.name)} · ${escapeHtml(employee.role_label)} · ${escapeHtml(employee.grade)}</option>`)
    .join("");
  state.employeeId = state.employees.some((item) => item.employee_id === preferredId)
    ? preferredId
    : state.employees[0]?.employee_id;
  els.picker.value = state.employeeId;
}

async function loadEmployee(employeeId = state.employeeId) {
  state.employeeId = employeeId;
  showLoading();
  const profile = await api(`/api/employee?id=${encodeURIComponent(employeeId)}`);
  state.profile = profile;
  renderEmployee(profile);
  els.loading.hidden = true;
  els.employeeView.hidden = false;
}

function showLoading() {
  els.employeeView.hidden = true;
  els.hrView.hidden = true;
  els.loading.hidden = false;
}

function renderEmployee(profile) {
  const employee = profile.employee;
  const openGaps = profile.skills.filter((skill) => skill.gap > 0).length;
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
  $("#readinessLabel").textContent = openGaps
    ? `Нужно закрыть ${openGaps} ${plural(openGaps, "разрыв", "разрыва", "разрывов")}`
    : "Требования грейда закрыты";
  els.dataSource.textContent = profile.data_source;

  const insight = $("#decisionInsight");
  insight.hidden = !profile.decision_insight;
  $("#decisionInsightText").textContent = profile.decision_insight || "";

  renderRecommendations(profile.recommendations);
  renderSkills(profile.skills);
  renderHistory(profile.history);
  const paused = profile.participation_paused;
  $("#participationButton").hidden = state.user?.role !== "employee";
  $("#participationButton").textContent = paused ? "Возобновить участие" : "Взять паузу";
  $("#participationStatus").textContent = paused
    ? "Участие на паузе. XP и навыки сохранены. Возвращайтесь, когда будете готовы. HR видит факт паузы в общей статистике."
    : "Можно заниматься в своём темпе или взять паузу без потери XP. Другие сотрудники не видят вашу вовлечённость.";
  renderGamification(profile.gamification);
  renderCoaching(profile.coaching);
  loadPlanner();
}

async function loadPlanner() {
  const profile = state.profile;
  const request = ++state.plannerRequest;
  state.selectedSteps = [];
  $("#simulateButton").disabled = true;
  $("#plannerOptions").innerHTML = "";
  $("#simulationResult").textContent = "";
  $("#plannerMessage").textContent = "Подбираем варианты под ваш бюджет…";
  try {
    const plan = await api(`/api/planner?id=${encodeURIComponent(profile.employee.employee_id)}&hours=${encodeURIComponent($("#plannerHours").value)}`);
    if (state.profile !== profile || request !== state.plannerRequest) return;
    $("#plannerMessage").textContent = plan.message;
    $("#plannerOptions").innerHTML = plan.options.map(item => `<label class="planner-option">
      <input type="checkbox" data-plan-event="${escapeHtml(item.event_id)}" />
      <span><strong>${escapeHtml(item.title)}</strong><small>${item.duration_hours} ч. · score ${item.score}/100 · готовность ${item.current_readiness}% → ${item.projected_readiness}% (+${item.readiness_delta} п.п.)</small></span>
    </label>`).join("");
    $$('[data-plan-event]').forEach(input => input.addEventListener("change", () => {
      if (input.checked && state.selectedSteps.length >= 3) {
        input.checked = false;
        showToast("В одном сценарии можно сравнить до трёх шагов");
        return;
      }
      state.selectedSteps = input.checked ? [...state.selectedSteps, input.dataset.planEvent]
        : state.selectedSteps.filter(id => id !== input.dataset.planEvent);
      $("#simulateButton").disabled = !state.selectedSteps.length;
      $("#simulationResult").textContent = "";
    }));
  } catch (error) {
    if (state.profile === profile && request === state.plannerRequest) $("#plannerMessage").textContent = error.message;
  }
}

$("#plannerHours").addEventListener("change", loadPlanner);
$("#simulateButton").addEventListener("click", async () => {
  const profile = state.profile;
  const request = state.plannerRequest;
  const selection = state.selectedSteps.join("|");
  $("#simulateButton").disabled = true;
  $("#simulationResult").textContent = "Рассчитываем…";
  try {
    const result = await api("/api/simulate", { method: "POST", body: JSON.stringify({
      employee_id: profile.employee.employee_id, event_ids: state.selectedSteps, hours: $("#plannerHours").value,
    }) });
    if (state.profile !== profile || request !== state.plannerRequest || selection !== state.selectedSteps.join("|")) return;
    $("#simulationResult").innerHTML = `<h3>Готовность: ${result.before}% → ${result.after}%</h3>
      <p>${result.hours} ч. · Полностью закрытых пробелов: ${result.closed_gaps}</p>
      <ol>${result.steps.map(step => `<li>${escapeHtml(step.title)}: ${step.before}% → ${step.after}% (+${step.delta} п.п.)</li>`).join("")}</ol>
      <p>${result.skills.map(skill => `${escapeHtml(skill.name)}: ${skill.before} → ${skill.after}`).join(" · ")}</p>
      <small>${escapeHtml(result.note)}</small>`;
  } catch (error) {
    if (state.profile === profile && request === state.plannerRequest && selection === state.selectedSteps.join("|")) $("#simulationResult").textContent = error.message;
  } finally {
    if (state.profile === profile && request === state.plannerRequest) $("#simulateButton").disabled = !state.selectedSteps.length;
  }
});

function renderGamification(game) {
  if (!game) {
    $("#questProgress").innerHTML = `<p class="coach-status">Для отображения XP и уровней перезапустите сервер Career Quest и обновите страницу. Сервер пока использует предыдущую версию приложения.</p>`;
    $("#questBadges").innerHTML = "";
    return;
  }
  $("#questProgress").innerHTML = `
    <div><strong>Уровень ${game.level}</strong><p>${game.xp} XP всего · ${game.completed} завершено</p>
      <progress aria-label="XP до следующего уровня" value="${game.level_xp}" max="${game.level_goal}"></progress>
      <small>${game.level_goal - game.level_xp} XP до уровня ${game.level + 1}</small></div>
    <div><strong>Квест недели · по желанию</strong><p>Личная цель: ${game.weekly_goal} активности</p>
      <progress aria-label="Квест недели" value="${Math.min(game.weekly_completed, game.weekly_goal)}" max="${game.weekly_goal}"></progress>
      <small>${game.weekly_completed} / ${game.weekly_goal}${game.weekly_completed >= game.weekly_goal ? " · Цель достигнута!" : " · Новая неделя начинается в понедельник"}</small></div>`;
  $("#questBadges").innerHTML = game.badges.map((badge) => `<div class="quest-badge ${badge.earned ? "earned" : ""}"><strong>${badge.earned ? "★" : "☆"} ${escapeHtml(badge.name)}</strong><small>${escapeHtml(badge.description)} · ${badge.earned ? "Получено" : "Пока закрыто"}</small></div>`).join("");
}

function renderCoaching(coach) {
  if (!coach) {
    $("#coachStatus").textContent = "Советы по навыкам появятся после перезапуска сервера и обновления страницы.";
    $("#coachAdvice").textContent = "";
    $("#coachAdvice").hidden = true;
    $("#coachPlans").innerHTML = "";
    $("#coachButton").disabled = true;
    $("#coachButton").textContent = "Нужен перезапуск сервера";
    return;
  }
  $("#coachStatus").textContent = coach.message;
  $("#coachAdvice").textContent = coach.advice;
  $("#coachAdvice").hidden = !coach.advice;
  $("#coachButton").disabled = !coach.ai_available || state.user?.role !== "employee";
  $("#coachButton").textContent = coach.ai_available ? "Получить совет AI" : "Локальный план";
  $("#coachPlans").innerHTML = coach.plans.map((plan) => `<article class="coach-card">
    <h3>${escapeHtml(plan.name)}</h3><p class="coach-status">${escapeHtml(plan.reason)}</p>
    <p>${escapeHtml(plan.practice)}</p><p>${escapeHtml(plan.checkpoint)}</p>
    <small>Следующий шаг: ${escapeHtml(plan.activity)}</small></article>`).join("");
}

$("#coachButton").addEventListener("click", async () => {
  const profile = state.profile;
  const button = $("#coachButton");
  button.disabled = true;
  $("#coachStatus").textContent = "AI составляет план…";
  try {
    const coach = await api("/api/coach", { method: "POST", body: JSON.stringify({ employee_id: profile.employee.employee_id }) });
    if (state.profile === profile) {
      profile.coaching = coach;
      renderCoaching(coach);
    }
  } catch (error) {
    if (state.profile === profile) {
      $("#coachStatus").textContent = error.message;
      button.disabled = false;
    }
  }
});

function renderRecommendations(recommendations) {
  const container = $("#recommendationGrid");
  if (!recommendations.length) {
    const hasGaps = state.profile?.skills.some(skill => skill.gap > 0);
    container.innerHTML = hasGaps
      ? `<div class="panel"><strong>Нужен новый шаг</strong><p>Пробелы ещё есть, но подходящих незавершённых активностей в каталоге нет. Обсудите практику с наставником или попросите HR дополнить каталог.</p></div>`
      : `<div class="panel"><strong>Маршрут завершён</strong><p>По текущим требованиям пробелов нет. Обсудите следующую цель с наставником.</p></div>`;
    return;
  }
  container.innerHTML = recommendations
    .map((item, index) => {
      const skills = item.affected_skills.map((skill) => `${escapeHtml(skill.name)} ${skill.before}→${skill.after}`).join(" · ");
      return `
        <article class="recommendation-card ${index === 0 ? "primary" : ""}">
          <div class="rank-row"><span class="rank">${index + 1}</span><span class="score"><b>${Math.round(item.score)}</b> / 100</span></div>
          <h3>${escapeHtml(item.title)}</h3>
          <div class="recommendation-meta"><span>${escapeHtml(typeLabel(item.type))}</span><span>≈ ${item.duration_hours} ч.</span><span>${skills}</span></div>
          <div class="impact-box"><span>Готовность к грейду</span><strong>${item.current_readiness}% → ${item.projected_readiness}%</strong></div>
          <div class="card-actions">
            <button data-details="${index}">Почему этот шаг</button>
            <button class="complete-button" data-complete="${escapeHtml(item.event_id)}">Отметить выполненным</button>
          </div>
        </article>`;
    })
    .join("");

  $$('[data-details]').forEach((button) => button.addEventListener("click", () => openExplanation(recommendations[Number(button.dataset.details)])));
  $$('[data-complete]').forEach((button) => {
    button.hidden = state.user?.role !== "employee";
    button.disabled = !!state.profile?.participation_paused;
    if (button.disabled) button.textContent = "Участие на паузе";
    button.addEventListener("click", () => completeActivity(button.dataset.complete, button));
  });
}

function typeLabel(type) {
  return ({ workshop: "Практикум", mentoring: "Менторство", course: "Курс", project: "Проект", assessment: "Аттестация", rotation: "Ротация" })[type] || "Активность";
}

function renderSkills(skills) {
  $("#skillsList").innerHTML = skills
    .map((skill) => `
      <div class="skill-row">
        <div class="skill-name"><strong>${escapeHtml(skill.name)}</strong><small>${skill.type === "hard" ? "hard skill" : "soft skill"}</small></div>
        <div class="skill-track" aria-label="${escapeHtml(skill.name)}: ${skill.current} из ${skill.required}">
          <i class="skill-fill" style="width:${skill.current / 5 * 100}%"></i>
          <i class="target-marker" style="left:${skill.required / 5 * 100}%"></i>
        </div>
        <div class="skill-level"><b>${skill.current}</b> / ${skill.required}${skill.gap ? ` · −${skill.gap}` : " · ✓"}</div>
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
  const profile = state.profile;
  button.disabled = true;
  button.textContent = "Обновляю прогресс…";
  try {
    const updated = await api("/api/complete", { method: "POST", body: JSON.stringify({ employee_id: state.employeeId, event_id: eventId }) });
    if (state.profile !== profile) return;
    if (updated.reward?.xp) burstConfetti(button.getBoundingClientRect());
    state.profile = updated;
    renderEmployee(updated);
    showToast(updated.reward
      ? (updated.reward.xp ? `+${updated.reward.xp} XP · Навыки и план обновлены!` : "Эта активность уже учтена")
      : "Активность учтена · Для включения XP перезапустите сервер");
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
  $("#scoreBreakdown").innerHTML = Object.entries(item.score_breakdown).map(([key, value]) => `<div class="score-part"><b>${value}%</b><small>${labels[key]}</small></div>`).join("");
  openModal(els.explanationModal);
}

async function showHr() {
  state.view = "hr";
  setActiveNav();
  showLoading();
  const data = await api("/api/hr");
  els.dataSource.textContent = data.data_source;
  renderHr(data);
  els.loading.hidden = true;
  els.hrView.hidden = false;
}

function renderHr(data) {
  const cards = [
    [data.employees, "Сотрудников в выборке", "Покрытие профилей"],
    [`${data.average_readiness}%`, "Средняя готовность", "К следующему грейду"],
    [`${data.completion_rate}%`, "Завершаемость", "По всем активностям"],
    [data.without_step, "Без следующего шага", "Нужно действие HR"],
    [data.paused_employees || 0, "Добровольная пауза", "Без сигнала о выпадении"],
  ];
  $("#kpiGrid").innerHTML = cards.map(([value, label, note]) => `<article class="kpi-card"><small>${label}</small><strong>${value}</strong><span>${note}</span></article>`).join("");
  const maxGap = Math.max(...data.skill_gaps.map((item) => item.total_gap), 1);
  $("#gapChart").innerHTML = data.skill_gaps.map((item) => `<div class="bar-row"><label>${escapeHtml(item.name)}</label><div class="bar-track"><i style="width:${item.total_gap / maxGap * 100}%"></i></div><b>${item.total_gap}</b></div>`).join("");
  const statusLabels = { completed: "Завершено", skipped: "Пропущено", declined: "Отказ" };
  const colors = { completed: "#168457", skipped: "#ef9854", declined: "#d9675d" };
  $("#participationChart").innerHTML = data.participation.map((item) => `<div class="participation-row"><i style="background:${colors[item.status] || "#87918b"}"></i><span>${statusLabels[item.status] || item.status}</span><strong>${item.percent}%</strong></div>`).join("");
  $("#watchTable").innerHTML = `<div class="watch-row header"><span>Сотрудник</span><span>Роль</span><span>Готовность</span><span>Участие</span></div>` + data.watchlist.map((item) => `<div class="watch-row"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.role)}</span><b>${item.readiness}%</b><span>${item.participation}%</span></div>`).join("");
}

async function showEmployeeView() {
  state.view = "employee";
  setActiveNav();
  await loadEmployee(state.employeeId);
}

function setActiveNav() {
  $("#employeeNav").classList.toggle("active", state.view === "employee");
  $("#hrNav").classList.toggle("active", state.view === "hr");
  els.pickerWrap.hidden = state.view === "hr" || state.user?.role !== "hr";
  els.pageTitle.textContent = state.view === "hr" ? "HR-аналитика" : "Карьерная траектория";
  $("#sidebar").classList.remove("open");
}

function openModal(modal) {
  closeModals();
  els.modalBackdrop.classList.add("open");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeModals() {
  els.modalBackdrop.classList.remove("open");
  $$(".modal.open").forEach((modal) => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  });
}

function selectFiles(files) {
  state.selectedFiles = [...files];
  els.uploadButton.disabled = !state.selectedFiles.length;
  els.selectedFiles.innerHTML = state.selectedFiles.map((file) => `<div class="file-chip"><span>${escapeHtml(file.name)}</span><b>${Math.ceil(file.size / 1024)} КБ</b></div>`).join("");
}

function parseCsv(text) {
  const delimiter = text.split(/\r?\n/, 1)[0].includes(";") ? ";" : ",";
  const rows = [];
  let row = [], value = "", quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '"' && text[index + 1] === '"' && quoted) { value += '"'; index += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === delimiter && !quoted) { row.push(value.trim()); value = ""; }
    else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      row.push(value.trim()); value = "";
      if (row.some(Boolean)) rows.push(row);
      row = [];
    } else value += char;
  }
  if (value || row.length) { row.push(value.trim()); rows.push(row); }
  const headers = rows.shift() || [];
  return rows.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""])));
}

async function uploadFiles() {
  els.uploadButton.disabled = true;
  els.uploadButton.textContent = "Проверяю схему…";
  try {
    const bundle = {};
    for (const file of state.selectedFiles) {
      const text = await file.text();
      const name = file.name.toLowerCase();
      if (name.endsWith(".csv")) {
        bundle.activity_history = parseCsv(text);
        continue;
      }
      const data = JSON.parse(text);
      if (data.employees || data.profiles || data.events || data.skills || data.history || data.activity_history) {
        Object.assign(bundle, data);
      } else if (name.includes("employee") || name.includes("profile")) bundle.employees = data;
      else if (name.includes("event")) bundle.events = data;
      else if (name.includes("skill")) bundle.skills = data;
      else if (Array.isArray(data) && data[0]?.employee_id) bundle.employees = data;
      else if (data.employee_id) bundle.employees = [data];
      else throw new Error(`Не удалось определить тип файла ${file.name}`);
    }
    const result = await api("/api/upload", { method: "POST", body: JSON.stringify(bundle) });
    closeModals();
    showToast(`Данные загружены: ${result.employees} профилей`);
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
  els.toast.style.background = error ? "#8d332d" : "#15241b";
  els.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 3000);
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

$("#employeeNav").addEventListener("click", () => showEmployeeView().catch(error => showToast(error.message, true)));
$("#hrNav").addEventListener("click", () => showHr().catch(error => showToast(error.message, true)));
els.picker.addEventListener("change", (event) => loadEmployee(event.target.value).catch(error => showToast(error.message, true)));
$("#mobileMenu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$("#openUploadButton").addEventListener("click", () => openModal(els.uploadModal));
els.modalBackdrop.addEventListener("click", closeModals);
$$('[data-close-modal]').forEach((button) => button.addEventListener("click", closeModals));
els.fileInput.addEventListener("change", (event) => selectFiles(event.target.files));
els.uploadButton.addEventListener("click", uploadFiles);
$("#dropZone").addEventListener("dragover", (event) => { event.preventDefault(); event.currentTarget.classList.add("dragging"); });
$("#dropZone").addEventListener("dragleave", (event) => event.currentTarget.classList.remove("dragging"));
$("#dropZone").addEventListener("drop", (event) => { event.preventDefault(); event.currentTarget.classList.remove("dragging"); selectFiles(event.dataTransfer.files); });
$("#resetButton").addEventListener("click", async () => {
  try {
  await api("/api/reset", { method: "POST", body: "{}" });
  state.employeeId = "E0028";
  await loadEmployees();
  await showHr();
  showToast("Демо-данные восстановлены");
  } catch (error) { showToast(error.message, true); }
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModals(); });

function showLogin() {
  state.user = null;
  state.csrf = null;
  state.profile = null;
  state.plannerRequest += 1;
  state.selectedSteps = [];
  $("#plannerOptions").innerHTML = "";
  $("#simulationResult").textContent = "";
  $("#plannerMessage").textContent = "";
  $("#plannerHours").value = "8";
  state.employees = [];
  state.selectedFiles = [];
  closeModals();
  $("#appShell").hidden = true;
  $("#loginView").hidden = false;
  $("#loginPassword").value = "";
  els.employeeView.hidden = true;
  els.hrView.hidden = true;
  els.picker.innerHTML = "";
  els.fileInput.value = "";
  els.selectedFiles.innerHTML = "";
  ["#watchTable", "#kpiGrid", "#gapChart", "#participationChart", "#recommendationGrid", "#skillsList", "#historyList", "#questProgress", "#questBadges", "#coachPlans", "#factorList", "#scoreBreakdown"].forEach(selector => $(selector).innerHTML = "");
  ["#employeeName", "#employeeRole", "#employeeTenure", "#employeeAvatar", "#coachAdvice", "#coachStatus", "#decisionInsightText", "#explanationTitle"].forEach(selector => $(selector).textContent = "");
  els.toast.classList.remove("show");
}

async function enterSession(session) {
  if (!session?.user || !["employee", "hr"].includes(session.user.role) || !session.csrf) {
    throw new Error("Запущена версия сервера без поддержки входа. Перезапустите server.py из папки career-quest.");
  }
  state.user = session.user;
  state.csrf = session.csrf;
  const hr = session.user.role === "hr";
  $("#hrNav").hidden = !hr;
  $("#employeeNav").lastElementChild.textContent = hr ? "Профили сотрудников" : "Мой путь";
  $("#openUploadButton").hidden = !hr;
  $("#resetButton").hidden = !hr;
  $("#sessionLabel").textContent = hr ? "HR" : "Сотрудник";
  $("#loginView").hidden = true;
  $("#appShell").hidden = false;
  if (hr) {
    await loadEmployees();
    await showHr();
  } else {
    state.employeeId = session.user.employee_id;
    await showEmployeeView();
  }
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#loginButton").disabled = true;
  $("#loginError").textContent = "";
  try {
    const session = await api("/api/login", { method: "POST", body: JSON.stringify({
      username: $("#loginUsername").value.trim(), password: $("#loginPassword").value,
    }) });
    $("#loginPassword").value = "";
    await enterSession(session);
  } catch (error) {
    showLogin();
    $("#loginError").textContent = error.message;
  } finally { $("#loginButton").disabled = false; }
});

$("#logoutButton").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
    showLogin();
  } catch (error) { showToast(error.message, true); }
});

$("#participationButton").addEventListener("click", async () => {
  $("#participationButton").disabled = true;
  try {
    await api("/api/participation", { method: "POST", body: JSON.stringify({ paused: !state.profile.participation_paused }) });
    await loadEmployee();
  } catch (error) { showToast(error.message, true); }
  finally { $("#participationButton").disabled = false; }
});

(async function init() {
  try {
    const session = await api("/api/session");
    await enterSession(session);
  } catch (error) {
    showLogin();
    $("#loginError").textContent = error.message === "Войдите в приложение" ? "" : `${error.message}. Если код обновлён, перезапустите сервер.`;
  }
})();
