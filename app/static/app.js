"use strict";

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) throw new Error(`${path} -> ${resp.status}`);
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

// --- Дашборд: статистика + график ---
async function loadStats() {
  const root = document.querySelector("#chart");
  if (!root) return;
  try {
    const data = await api("/api/stats?days=30");
    for (const [key, el] of Object.entries({
      total: '[data-stat="total"]',
      favorites: '[data-stat="favorites"]',
      searches: '[data-stat="searches"]',
    })) {
      const node = document.querySelector(el);
      if (node) node.textContent = data[key];
    }
    const max = Math.max(1, ...data.days.map((d) => d.count));
    root.innerHTML = "";
    data.days.forEach((d) => {
      const bar = document.createElement("div");
      bar.className = "bar";
      bar.style.height = `${Math.max(2, Math.round((d.count / max) * 100))}%`;
      bar.innerHTML = `<span class="tip">${d.date}: ${d.count}</span>`;
      root.appendChild(bar);
    });
  } catch (e) {
    console.error(e);
  }
}

// --- Избранное ---
async function toggleFavorite(btn) {
  const hhId = btn.dataset.hhId;
  try {
    const data = await api(`/api/vacancies/${hhId}/favorite`, { method: "POST" });
    btn.classList.toggle("on", data.is_favorite);
  } catch (e) {
    console.error(e);
  }
}

function initFavorites() {
  document.querySelectorAll(".fav-btn").forEach((btn) => {
    btn.addEventListener("click", () => toggleFavorite(btn));
  });
}

// --- Статус вакансии: не интересует / Отклик / Отказ ---
function initStatusSelect() {
  document.querySelectorAll(".status-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      try {
        const data = await api(`/api/vacancies/${sel.dataset.hhId}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: sel.value }),
        });
        const cell = document.querySelector(`[data-applied="${sel.dataset.hhId}"]`);
        if (cell) cell.textContent = data.applied_at ? fmtDate(data.applied_at) : "—";
      } catch (e) {
        console.error(e);
        location.reload();
      }
    });
  });
}

// --- Поиски: добавление, автодополнение регионов, вкл/выкл, удаление ---
let areaDebounce = null;
let selectedArea = null;

function initAreaSuggest() {
  const input = document.getElementById("area-input");
  const list = document.getElementById("area-suggest");
  if (!input || !list) return;
  input.addEventListener("input", () => {
    clearTimeout(areaDebounce);
    const q = input.value.trim();
    selectedArea = null;
    const idInput = document.querySelector('input[name="area_id"]');
    const nameInput = document.querySelector('input[name="area_name"]');
    if (idInput) idInput.value = "";
    if (nameInput) nameInput.value = "";
    if (q.length < 2) { list.style.display = "none"; list.innerHTML = ""; return; }
    areaDebounce = setTimeout(async () => {
      try {
        const items = await api(`/api/areas?q=${encodeURIComponent(q)}`);
        list.innerHTML = "";
        items.forEach((item) => {
          const li = document.createElement("li");
          li.textContent = item.name;
          li.addEventListener("click", () => {
            input.value = item.name;
            if (idInput) idInput.value = item.id;
            if (nameInput) nameInput.value = item.name;
            selectedArea = item;
            list.style.display = "none";
            list.innerHTML = "";
          });
          list.appendChild(li);
        });
        list.style.display = items.length ? "block" : "none";
      } catch (e) {
        console.error(e);
      }
    }, 250);
  });
  document.addEventListener("click", (e) => {
    if (!list.contains(e.target) && e.target !== input) list.style.display = "none";
  });
}

function initSearchForm() {
  const form = document.getElementById("search-form");
  const msg = document.getElementById("form-msg");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = new FormData(form);
    const payload = {
      title: data.get("title"),
      query: data.get("query"),
      area_id: data.get("area_id") || "",
      area_name: data.get("area_name") || "",
      title_only: data.get("title_only") ? true : false,
    };
    const searchId = data.get("search_id");
    const path = searchId ? `/api/searches/${searchId}` : "/api/searches";
    const method = searchId ? "PUT" : "POST";
    try {
      await api(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (msg) msg.textContent = searchId ? "Сохранено ✓" : "Добавлено ✓";
      setTimeout(() => location.reload(), 400);
    } catch (err) {
      if (msg) msg.textContent = `Ошибка: ${err.message}`;
    }
  });
}

function resetSearchForm() {
  const form = document.getElementById("search-form");
  const submit = document.getElementById("search-submit");
  const cancel = document.getElementById("search-cancel");
  const msg = document.getElementById("form-msg");
  if (!form) return;
  form.reset();
  if (submit) submit.textContent = "Добавить поиск";
  if (cancel) cancel.style.display = "none";
  if (msg) msg.textContent = "";
  selectedArea = null;
}

function initSearchEdit() {
  const form = document.getElementById("search-form");
  if (!form) return;
  const submit = document.getElementById("search-submit");
  const cancel = document.getElementById("search-cancel");
  const msg = document.getElementById("form-msg");
  const areaInput = document.getElementById("area-input");
  const searchId = form.querySelector('input[name="search_id"]');
  const areaIdInput = form.querySelector('input[name="area_id"]');
  const areaNameInput = form.querySelector('input[name="area_name"]');
  const titleInput = form.querySelector('input[name="title"]');
  const titleOnlyInput = form.querySelector('input[name="title_only"]');

  if (cancel) cancel.addEventListener("click", resetSearchForm);

  document.querySelectorAll(".search-edit").forEach((btn) => {
    btn.addEventListener("click", () => {
      searchId.value = btn.dataset.id;
      titleInput.value = btn.dataset.title || "";
      form.querySelector('input[name="query"]').value = btn.dataset.query || "";
      if (areaInput) areaInput.value = btn.dataset.areaName || "";
      areaIdInput.value = btn.dataset.areaId || "";
      areaNameInput.value = btn.dataset.areaName || "";
      if (titleOnlyInput) titleOnlyInput.checked = btn.dataset.titleOnly === "1";
      if (submit) submit.textContent = "Сохранить изменения";
      if (cancel) cancel.style.display = "";
      if (msg) msg.textContent = `Редактирование: ${btn.dataset.title}`;
      form.scrollIntoView({ behavior: "smooth" });
      titleInput.focus();
    });
  });
}

function initPollNow() {
  const btn = document.getElementById("poll-now");
  const msg = document.getElementById("poll-msg");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    if (msg) msg.textContent = "Поллинг запущен, страница обновится через ~15 с…";
    try {
      await api("/api/poll/run", { method: "POST" });
    } catch (e) {
      if (msg) msg.textContent = `Ошибка: ${e.message}`;
      btn.disabled = false;
      return;
    }
    setTimeout(() => location.reload(), 15000);
  });
}

function initSearchControls() {
  document.querySelectorAll(".search-toggle").forEach((cb) => {
    cb.addEventListener("change", async () => {
      try {
        await api(`/api/searches/${cb.dataset.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ active: cb.checked }),
        });
      } catch (e) {
        console.error(e);
      }
    });
  });
  document.querySelectorAll(".search-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Удалить этот поиск?")) return;
      try {
        await api(`/api/searches/${btn.dataset.id}`, { method: "DELETE" });
        location.reload();
      } catch (e) {
        console.error(e);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  loadStats();
  initFavorites();
  initStatusSelect();
  initAreaSuggest();
  initSearchForm();
  initSearchEdit();
  initPollNow();
  initSearchControls();
});
