const state = {
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
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
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
}

function renderRecommendations(recommendations) {
  const container = $("#recommendationGrid");
  if (!recommendations.length) {
    container.innerHTML = `<div class="panel"><strong>Маршрут завершён</strong><p>Текущие навыки уже соответствуют следующему грейду. HR может добавить новую цель.</p></div>`;
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
  button.disabled = true;
  button.textContent = "Обновляю прогресс…";
  try {
    const updated = await api("/api/complete", { method: "POST", body: JSON.stringify({ employee_id: state.employeeId, event_id: eventId }) });
    burstConfetti(button.getBoundingClientRect());
    state.profile = updated;
    renderEmployee(updated);
    showToast("Активность учтена · траектория пересчитана");
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
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
  els.pickerWrap.hidden = state.view === "hr";
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
    await showEmployeeView();
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

$("#employeeNav").addEventListener("click", showEmployeeView);
$("#hrNav").addEventListener("click", showHr);
els.picker.addEventListener("change", (event) => loadEmployee(event.target.value));
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
  await api("/api/reset", { method: "POST", body: "{}" });
  state.employeeId = "E0028";
  await loadEmployees();
  await showEmployeeView();
  showToast("Демо-данные восстановлены");
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModals(); });

(async function init() {
  try {
    await loadEmployees();
    await loadEmployee(state.employeeId);
  } catch (error) {
    els.loading.innerHTML = `<strong>Не удалось подключиться к серверу</strong><p>${escapeHtml(error.message)}. Запустите приложение командой <code>python3 server.py</code>.</p>`;
  }
})();
