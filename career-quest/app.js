const state = {
  user: null,
  csrf: null,
  plannerRequest: 0,
  profileRequest: 0,
  aiRequest: 0,
  selectedSteps: [],
  employees: [],
  employeeId: "E0028",
  view: "employee",
  profile: null,
  selectedFiles: [],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
// Adapted from fuse's cross-tab/session revalidation; no tokens in messages.
const authChannel = typeof BroadcastChannel === "function" ? new BroadcastChannel("cq-auth") : null;
if (authChannel) authChannel.onmessage = () => {
  if (state.user) { showLogin(); $("#loginError").textContent = "Сессия изменена в другой вкладке. Войдите снова."; }
};
window.addEventListener("focus", async () => {
  if (!state.user) return;
  try {
    const session = await api("/api/session");
    if (session.csrf !== state.csrf) showLogin();
  } catch (_) { /* api handles expired sessions; network failure is not logout */ }
});

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
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "A²";
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
  state.profile = null;
  const request = ++state.profileRequest;
  showLoading();
  const profile = await api(`/api/employee?id=${encodeURIComponent(employeeId)}`);
  if (request !== state.profileRequest || state.view !== "employee") return;
  state.profile = profile;
  renderEmployee(profile);
  els.loading.hidden = true;
  els.employeeView.hidden = false;
}

function showLoading() {
  els.loading.querySelector(".loader").hidden = false;
  els.loading.querySelector("p").textContent = "Загружаем данные…";
  els.employeeView.hidden = true;
  els.hrView.hidden = true;
  els.loading.hidden = false;
}

function renderEmployee(profile) {
  $("#viewingBanner").hidden = state.user?.role !== "hr";
  const employee = profile.employee;
  const openGaps = profile.skills.filter((skill) => skill.gap > 0).length;
  $("#employeeAvatar").textContent = initials(employee.name);
  $("#employeeName").textContent = employee.name || employee.employee_id;
  $("#employeeRole").textContent = profile.role_label || employee.role;
  $("#employeeTenure").textContent = `${employee.tenure_months || 0} ${plural(employee.tenure_months || 0, "месяц", "месяца", "месяцев")} в компании`;
  $("#currentGrade").textContent = employee.grade;
  $("#targetGrade").textContent = profile.target_grade;
  $("#skillsTargetGrade").textContent = profile.target_grade;
  const readiness = profile.readiness ?? 0;
  $("#readinessValue").textContent = profile.requirements_missing ? "—" : `${Math.round(readiness)}%`;
  $("#readinessRing").style.background = `conic-gradient(var(--lime) 0deg, var(--lime) ${readiness * 3.6}deg, rgba(255,255,255,.12) ${readiness * 3.6}deg)`;
  $("#trajectoryLine").style.width = `${readiness}%`;
  $("#readinessLabel").textContent = profile.requirements_missing ? "Нет требований для расчёта" : openGaps
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
  renderDecision(profile);

}

function renderDecision(profile) {
  const hybrid = profile.decision_source === "hybrid_ai";
  $("#decisionSource").textContent = hybrid ? "HYBRID AI · ПРОВЕРЕНО АЛГОРИТМОМ" : (["not_requested", "ai_disabled", "no_candidates", undefined, null].includes(profile.fallback_reason) ? "АЛГОРИТМИЧЕСКИЙ РЕЖИМ" : "РЕЗЕРВНЫЙ АЛГОРИТМИЧЕСКИЙ РЕЖИМ");
  const messages = {ai_disabled: "Внешний AI отключён.", no_candidates: "Нет допустимых кандидатов.",
    timeout: "AI не ответил вовремя.", network_error: "AI недоступен.", invalid_response: "Ответ AI не прошёл проверку.",
    state_changed: "Профиль изменился во время запроса.", ai_busy: "AI занят.", request_in_progress: "AI уже обрабатывает этот профиль."};
  $("#decisionStatus").textContent = hybrid
    ? `LLM выбрал порядок допустимых шагов; факты и score рассчитаны локально. ${profile.cache_hit ? "Ответ из кеша." : `Время AI: ${profile.ai_latency_ms} мс.`}`
    : `${messages[profile.fallback_reason] || ""} Используется многофакторный детерминированный рейтинг.`;
}

async function refreshAiRecommendations(profile) {
  const request = ++state.aiRequest;
  const hours = $("#plannerHours").value;
  $("#decisionStatus").textContent = "Профиль готов. AI сравнивает допустимые шаги; пока доступен алгоритмический результат…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 9500);
  try {
    const result = await api("/api/recommendations/ai", {method: "POST", signal: controller.signal,
      body: JSON.stringify({employee_id: profile.employee.employee_id, hours})});
    if (state.profile !== profile || request !== state.aiRequest || hours !== $("#plannerHours").value) return;
    if (result.revision !== profile.revision) {
      profile.fallback_reason = "state_changed";
      renderDecision(profile);
      return;
    }
    Object.assign(profile, result);
    renderRecommendations(profile.recommendations);
    renderDecision(profile);
    if (profile.ai_used) {
      $("#decisionInsight").hidden = true;
      if (els.explanationModal.classList.contains("open")) closeModals();
    }
  } catch (error) {
    if (state.profile === profile && request === state.aiRequest && hours === $("#plannerHours").value) {
      profile.fallback_reason = error.name === "AbortError" ? "timeout" : "network_error";
      renderDecision(profile);
    }
  } finally { clearTimeout(timer); }
}

async function loadPlanner() {
  const profile = state.profile;
  const request = ++state.plannerRequest;
  state.aiRequest += 1;
  closeModals();
  state.selectedSteps = [];
  $("#simulateButton").disabled = true;
  $("#plannerOptions").innerHTML = "";
  $("#simulationResult").textContent = "";
  $("#plannerMessage").textContent = "Подбираем варианты под ваш бюджет…";
  try {
    const plan = await api(`/api/planner?id=${encodeURIComponent(profile.employee.employee_id)}&hours=${encodeURIComponent($("#plannerHours").value)}`);
    if (state.profile !== profile || request !== state.plannerRequest) return;
    $("#plannerMessage").textContent = plan.message;
    profile.recommendations = plan.options.slice(0, 3);
    profile.ai_used = false;
    profile.decision_source = "deterministic_fallback";
    profile.fallback_reason = "not_requested";
    renderRecommendations(profile.recommendations);
    renderDecision(profile);
    if (profile.ai_available && plan.options.length) refreshAiRecommendations(profile);
    $("#plannerOptions").innerHTML = plan.options.map(item => `<label class="planner-option">
      <input type="checkbox" data-plan-event="${escapeHtml(item.event_id)}" />
      <span><strong>${escapeHtml(item.title)}</strong><small>${item.duration_hours} ч. · score ${item.score}/100 · покрытие ${item.current_readiness}% → ${item.projected_readiness}% (+${item.readiness_delta} п.п.)</small></span>
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
    $("#simulationResult").innerHTML = `<h3>Покрытие требований: ${result.before}% → ${result.after}%</h3>
      <p>${result.hours} ч. · Полностью закрытых пробелов: ${result.closed_gaps} · Улучшено навыков: ${result.improved_skills_count}</p>
      <p>Оставшихся пробелов: ${result.remaining_gaps.length}</p>
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
    $("#questProgress").innerHTML = `<p class="coach-status">Для отображения XP и уровней перезапустите сервер A^2SCEND и обновите страницу. Сервер пока использует предыдущую версию приложения.</p>`;
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
    if (state.profile?.requirements_missing) {
      container.innerHTML = `<div class="panel"><strong>Нет требований для расчёта</strong><p>HR нужно добавить требования следующего грейда для этой роли.</p></div>`;
      return;
    }
    const hasGaps = state.profile?.skills.some(skill => skill.gap > 0);
    container.innerHTML = hasGaps
      ? `<div class="panel"><strong>Нужен новый шаг</strong><p>В текущем бюджете нет подходящих незавершённых активностей. Можно увеличить время; если каталог пуст, его нужно дополнить. Обсудите практику с наставником или попросите HR дополнить каталог.</p></div>`
      : `<div class="panel"><strong>Маршрут завершён</strong><p>По текущим требованиям пробелов нет. Обсудите следующую цель с наставником.</p></div>`;
    return;
  }
  container.innerHTML = recommendations
    .map((item, index) => {
      const skills = item.affected_skills.map((skill) => `${escapeHtml(skill.name)} ${skill.before}→${skill.after}`).join(" · ");
      return `
        <article class="recommendation-card ${index === 0 ? "primary" : ""}">
          <div class="rank-row"><span class="rank">${index === 0 ? "Следующий лучший шаг" : `Альтернатива ${index}`}</span><span class="score"><b>${Math.round(item.score)}</b> / 100</span></div>
          <h3>${escapeHtml(item.title)}</h3><p class="card-reason">${escapeHtml(item.factors[0]?.text || "Шаг закрывает текущий разрыв.")}</p><small>${state.profile?.ai_used ? "Hybrid AI" : "Алгоритмический подбор"}</small>
          <div class="recommendation-meta"><span>${escapeHtml(typeLabel(item.type))}</span><span>≈ ${item.duration_hours} ч.</span><span>${skills}</span></div>
          <div class="impact-box"><span>Покрытие требований</span><strong>${item.current_readiness}% → ${item.projected_readiness}% · +${item.readiness_delta} п.п.</strong></div>
          <div class="card-actions">
            <button data-details="${index}">Почему этот шаг</button>
            <button data-simulate="${escapeHtml(item.event_id)}">Смоделировать</button>
            <button class="complete-button" data-complete="${escapeHtml(item.event_id)}">Отметить выполненным</button>
          </div>
        </article>`;
    })
    .join("");

  $$('[data-details]').forEach((button) => button.addEventListener("click", () => openExplanation(recommendations[Number(button.dataset.details)])));
  $$('[data-simulate]').forEach(button => button.addEventListener("click", () => {
    state.selectedSteps = [button.dataset.simulate];
    $$('[data-plan-event]').forEach(input => { input.checked = input.dataset.planEvent === button.dataset.simulate; });
    $("#simulateButton").disabled = false;
    $("#simulateButton").click();
    $("#plannerTitle").scrollIntoView({block: "start"});
  }));
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
  $("#explanationSource").textContent = state.profile.ai_used ? "Hybrid AI: порядок LLM + локальная проверка" : "Алгоритмический подбор: четыре фактора";
  $("#aiExplanation").hidden = !item.ai_explanation;
  $("#aiSummary").textContent = "AI определил порядок. Ниже — проверяемые локальные факторы; свободные утверждения модели не используются как факты.";
  $("#aiEvidence").textContent = (item.evidence || []).map(factor => `${factor.label}: ${factor.text}`).join(" ");
  const alternative = item.comparison;
  $("#aiComparison").textContent = alternative
    ? `Сравнение с «${alternative.title}»: длительность ${item.duration_hours} / ${alternative.duration_hours} ч.; прирост покрытия ${item.readiness_delta} / ${alternative.readiness_delta} п.п. Факторы текущего / альтернативы: ${Object.entries(item.score_breakdown).map(([key, value]) => `${key}: ${value} / ${alternative.score_breakdown[key]}`).join("; ")}. Это локальные данные, а порядок — выбор модели.`
    : "Это последний вариант в выбранном списке.";
  $("#factorList").innerHTML = item.factors.map((factor, index) => `<div class="factor-item"><span>${index + 1}</span><div><strong>${escapeHtml(factor.label)}</strong><p>${escapeHtml(factor.text)}</p></div></div>`).join("");
  const labels = { grade_relevance: "Грейд и gap", trajectory_impact: "Влияние", history_fit: "История", feasibility: "Выполнимость" };
  $("#scoreBreakdown").innerHTML = Object.entries(item.score_breakdown).map(([key, value]) => `<div class="score-part"><b>${value}%</b><small>${labels[key]}</small></div>`).join("");
  openModal(els.explanationModal);
}

async function showHr() {
  state.profile = null;
  state.profileRequest += 1;
  state.aiRequest += 1;
  state.view = "hr";
  setActiveNav();
  showLoading();
  const data = await api("/api/hr");
  els.dataSource.textContent = data.data_source;
  if (state.view !== "hr") return;
  renderHr(data);
  const audit = await api("/api/security");
  if (state.view !== "hr") return;
  $("#auditPanel").hidden = false;
  $("#auditEvents").innerHTML = audit.audit_events.map(row => `<p>${escapeHtml(row.timestamp)} · ${escapeHtml(row.action)} · ${escapeHtml(row.actor)} · ${escapeHtml(row.detail)}</p>`).join("") || "Пока нет событий доступа HR.";
  els.loading.hidden = true;
  els.hrView.hidden = false;
}

function renderHr(data) {
  const cards = [
    [data.employees, "Сотрудников в выборке", "Покрытие профилей"],
    [data.average_readiness == null ? "—" : `${data.average_readiness}%`, "Среднее покрытие", "Только профили с требованиями"],
    [`${data.completion_rate}%`, "Завершаемость", "Завершённые / все записи истории"],
    [data.without_step, "Без следующего шага", "Нужно действие HR"],
    [data.paused_employees || 0, "Добровольная пауза", "Без сигнала о выпадении"],
    [data.requirements_missing || 0, "Нет требований", "Качество данных: готовность недоступна"],
  ];
  $("#kpiGrid").innerHTML = cards.map(([value, label, note]) => `<article class="kpi-card"><small>${label}</small><strong>${value}</strong><span>${note}</span></article>`).join("");
  const maxGap = Math.max(...data.skill_gaps.map((item) => item.total_gap), 1);
  $("#gapChart").innerHTML = data.skill_gaps.map((item) => `<div class="bar-row"><label>${escapeHtml(item.name)}</label><div class="bar-track"><i style="width:${item.total_gap / maxGap * 100}%"></i></div><b>${item.total_gap}</b></div>`).join("");
  const statusLabels = { completed: "Завершено", skipped: "Пропущено", declined: "Отказ" };
  const colors = { completed: "#168457", skipped: "#ef9854", declined: "#d9675d" };
  $("#participationChart").innerHTML = data.participation.map((item) => `<div class="participation-row"><i style="background:${colors[item.status] || "#87918b"}"></i><span>${statusLabels[item.status] || item.status}</span><strong>${item.percent}%</strong></div>`).join("");
  $("#watchTable").innerHTML = `<div class="watch-row header"><span>Сотрудник</span><span>Роль</span><span>Готовность</span><span>Участие</span></div>` + data.watchlist.map((item) => `<div class="watch-row"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.role)}</span><b>${item.readiness}%</b><span>${item.participation}%</span></div>`).join("");
  $("#withoutStepTable").innerHTML = `<thead><tr><th>Сотрудник</th><th>Роль / грейд</th><th>Готовность</th><th>Причина</th></tr></thead><tbody>${(data.employees_without_step || []).map(item => `<tr><td>${escapeHtml(item.name)} (${escapeHtml(item.employee_id)})</td><td>${escapeHtml(item.role)} / ${escapeHtml(item.grade)}</td><td>${item.readiness == null ? "—" : `${item.readiness}%`}</td><td>${escapeHtml(item.reason)}</td></tr>`).join("") || '<tr><td colspan="4">У всех сотрудников есть следующий шаг.</td></tr>'}</tbody>`;
  $("#activityParticipationTable").innerHTML = `<thead><tr><th>Активность</th><th>Подходит по роли</th><th>Участники</th><th>Завершили</th><th>Пропустили</th><th>Отказались</th><th>Завершённость</th></tr></thead><tbody>${(data.activity_participation || []).map(item => `<tr><td>${escapeHtml(item.title)}</td><td>${item.eligible_employees}</td><td>${item.participants}</td><td>${item.completed}</td><td>${item.skipped}</td><td>${item.declined}</td><td>${item.completion_rate}%</td></tr>`).join("")}</tbody>`;
}

async function showEmployeeView() {
  state.view = "employee";
  setActiveNav();
  await loadEmployee(state.employeeId);
}

function setActiveNav() {
  $("#auditPanel").hidden = state.view !== "hr";
  $("#employeeNav").classList.toggle("active", state.view === "employee");
  $("#hrNav").classList.toggle("active", state.view === "hr");
  els.pickerWrap.hidden = state.view === "hr" || state.user?.role !== "hr";
  els.pageTitle.textContent = state.view === "hr" ? "HR-аналитика" : "Карьерная траектория";
  $("#sidebar").classList.remove("open");
}

let modalTrigger = null;
function openModal(modal) {
  closeModals();
  modalTrigger = document.activeElement;
  els.modalBackdrop.hidden = false;
  modal.hidden = false;
  els.modalBackdrop.classList.add("open");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  $("#appShell").inert = true;
  modal.querySelector("button, input, select")?.focus();
}
function closeModals() {
  els.modalBackdrop.hidden = true;
  els.modalBackdrop.classList.remove("open");
  $$(".modal").forEach(modal => { modal.hidden = true; modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true"); });
  $("#appShell").inert = false;
  if (modalTrigger?.isConnected) modalTrigger.focus();
  modalTrigger = null;
}
document.addEventListener("keydown", event => {
  const modal = $(".modal.open");
  if (event.key !== "Tab" || !modal) return;
  const nodes = [...modal.querySelectorAll('button:not(:disabled), input, select, [tabindex="0"]')].filter(node => !node.hidden);
  const first = nodes[0], last = nodes.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});

let importGeneration = 0;
async function selectFiles(files) {
  state.selectedFiles = [...files];
  state.importPayload = null;
  els.uploadButton.disabled = true;
  const generation = ++importGeneration;
  els.selectedFiles.innerHTML = state.selectedFiles.map(file => `<div class="file-chip">${escapeHtml(file.name)} · ${Math.ceil(file.size / 1024)} КБ</div>`).join("");
  $("#importPreview").textContent = "Проверяем файлы без изменения данных…";
  try {
    const payload = {mode: $("#importMode").value, files: await Promise.all(state.selectedFiles.map(async file => ({name: file.name, content: await file.text()})))};
    const result = await api("/api/upload/preview", {method: "POST", body: JSON.stringify(payload)});
    if (generation !== importGeneration) return;
    state.importPayload = {...payload, revision: result.revision};
    $("#importPreview").textContent = `${result.mode === "replace" ? "Замена всего набора" : "Дополнение; история сохраняется"}. Найдено: ${Object.entries(result.counts).map(([key, count]) => `${key}: ${count}`).join(", ")}. ${result.warnings.join(" ")}`;
    els.uploadButton.disabled = false;
    els.uploadButton.textContent = "Применить проверенный импорт";
  } catch (error) { if (generation === importGeneration) $("#importPreview").textContent = `Ошибка: ${error.message}`; }
}
$("#importMode").addEventListener("change", () => { if (state.selectedFiles.length) selectFiles(state.selectedFiles); });
async function uploadFiles() {
  if (!state.importPayload) return;
  els.uploadButton.disabled = true;
  try {
    const result = await api("/api/upload", {method: "POST", body: JSON.stringify(state.importPayload)});
    state.importPayload = null;
    closeModals();
    $("#importWarnings").textContent = (result.warnings || []).join("\n");
    $("#importWarnings").hidden = !result.warnings?.length;
    await loadEmployees(state.employeeId);
    await showHr();
    showToast("Импорт применён атомарно");
  } catch (error) { $("#importPreview").textContent = `Ошибка: ${error.message}. Повторно выберите файлы для проверки.`; }
}

function showToast(message, error = false) {
  if (error && !els.loading.hidden) {
    els.loading.querySelector(".loader").hidden = true;
    els.loading.querySelector("p").textContent = `Не удалось загрузить данные: ${message}. Повторите выбор раздела.`;
  }
  els.toast.textContent = message;
  els.toast.style.background = error ? "#8d332d" : "#15241b";
  els.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 3000);
}

function burstConfetti(rect) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
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
  clearTimeout(state.expiryTimer);
  state.aiRequest += 1;
  state.profileRequest += 1;
  importGeneration += 1;
  state.importPayload = null;
  $("#auditEvents").textContent = "";
  $("#auditPanel").hidden = true;
  state.user = null;
  state.csrf = null;
  state.profile = null;
  state.plannerRequest += 1;
  state.selectedSteps = [];
  $("#plannerOptions").innerHTML = "";
  $("#simulationResult").textContent = "";
  $("#plannerMessage").textContent = "";
  $("#plannerHours").value = "8";
  $("#withoutStepTable").innerHTML = "";
  $("#activityParticipationTable").innerHTML = "";
  $("#importWarnings").textContent = "";
  ["#aiSummary", "#aiEvidence", "#aiComparison", "#decisionStatus"].forEach(selector => $(selector).textContent = "");
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
  clearTimeout(state.expiryTimer);
  state.expiryTimer = setTimeout(() => {
    showLogin();
    $("#loginError").textContent = "Сессия истекла. Войдите снова.";
  }, Math.max(0, session.expires * 1000 - Date.now()));
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
    authChannel?.postMessage("changed");
  } catch (error) {
    showLogin();
    $("#loginError").textContent = error.message;
  } finally { $("#loginButton").disabled = false; }
});

$("#logoutButton").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
    authChannel?.postMessage("changed");
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
