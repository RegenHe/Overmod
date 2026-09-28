"use strict";

const state = {
  token: sessionStorage.getItem("overmod-admin-token") || "",
  mods: [],
  submissions: [],
  adminPage: 1,
};
const ADMIN_PAGE_SIZE = 40;
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (!response.ok) {
    const detail = payload && payload.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || String(item)).join("；")
      : (detail || `HTTP ${response.status}`);
    throw new Error(message);
  }
  return payload;
}

function setStatus(element, message, isError = false) {
  element.textContent = message || "";
  element.classList.toggle("is-error", isError);
}

function showWorkspace(authenticated) {
  $("admin-login").hidden = authenticated;
  $("admin-workspace").hidden = !authenticated;
  $("admin-logout").hidden = !authenticated;
}

async function verify() {
  if (!state.token) return showWorkspace(false);
  try {
    await api("/api/v1/admin/session", { method: "POST" });
    showWorkspace(true);
    await Promise.all([loadMods(), loadSubmissions()]);
  } catch (error) {
    state.token = "";
    sessionStorage.removeItem("overmod-admin-token");
    showWorkspace(false);
    setStatus($("login-status"), `验证失败：${error.message}`, true);
  }
}

function resetEditor() {
  $("mod-form").reset();
  $("mod-id").value = "";
  $("submission-id").value = "";
  $("field-download-label").value = "项目页面";
  $("field-enabled").checked = true;
  $("field-featured").checked = false;
  $("editor-heading").textContent = "添加模组";
  $("edit-cancel").hidden = true;
  updateIdentityFields();
  setStatus($("editor-status"), "");
}

function fillEditor(item) {
  $("field-name").value = item.name;
  $("field-author").value = item.author;
  $("field-version").value = item.version;
  $("field-level-key").value = item.level_key;
  $("field-level-set-uid").value = item.level_set_uid || "";
  $("field-scene-name").value = item.scene_name || "";
  $("field-mod-type").value = item.mod_type;
  updateIdentityFields();
  $("field-description").value = item.description;
  $("field-download-label").value = item.download_label;
  $("field-download-url").value = item.download_url;
  $("field-download-instructions").value = item.download_instructions;
  $("field-featured").checked = Boolean(item.featured);
}

function edit(mod) {
  fillEditor(mod);
  $("mod-id").value = mod.id;
  $("submission-id").value = "";
  $("field-enabled").checked = mod.enabled;
  $("editor-heading").textContent = `编辑：${mod.name}`;
  $("edit-cancel").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function review(submission) {
  fillEditor(submission);
  const target = state.mods.find((mod) => mod.id === submission.target_mod_id);
  $("field-featured").checked = Boolean(target && target.featured);
  $("mod-id").value = "";
  $("submission-id").value = submission.id;
  $("field-enabled").checked = true;
  $("editor-heading").textContent = `${submission.target_mod_id ? "审核修改" : "审核新增"}：${submission.name}`;
  $("edit-cancel").hidden = false;
  setStatus($("editor-status"), "可以先修改投稿内容，再保存并通过审核。");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderMods() {
  const list = $("admin-list");
  list.replaceChildren();
  const pageCount = Math.max(1, Math.ceil(state.mods.length / ADMIN_PAGE_SIZE));
  state.adminPage = Math.min(Math.max(1, state.adminPage), pageCount);
  const pageStart = (state.adminPage - 1) * ADMIN_PAGE_SIZE;
  const pageMods = state.mods.slice(pageStart, pageStart + ADMIN_PAGE_SIZE);
  $("admin-count").textContent = state.mods.length
    ? `${state.mods.length} 项 · 第 ${state.adminPage}/${pageCount} 页`
    : "0 项";
  pageMods.forEach((mod) => {
    const row = document.createElement("article");
    row.className = `admin-row${mod.enabled ? "" : " is-hidden"}`;
    const details = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = mod.name;
    const meta = document.createElement("p");
    meta.textContent = `${mod.mod_type === "map" ? "地图" : "工具"} · ${mod.author || "未填写作者"} · ${mod.version || "未填写版本"} · ${mod.enabled ? "公开显示" : "已隐藏"}${mod.featured ? " · 推荐" : ""}${mod.level_key ? ` · ${mod.level_key}` : ""}`;
    details.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "admin-actions";
    const editButton = document.createElement("button");
    editButton.className = "row-button";
    editButton.type = "button";
    editButton.textContent = "编辑";
    editButton.addEventListener("click", () => edit(mod));
    const deleteButton = document.createElement("button");
    deleteButton.className = "row-button danger-button";
    deleteButton.type = "button";
    deleteButton.textContent = "删除";
    deleteButton.addEventListener("click", async () => {
      if (!confirm(`确定删除“${mod.name}”吗？`)) return;
      try {
        await api(`/api/v1/admin/mods/${mod.id}`, { method: "DELETE" });
        await loadMods();
      } catch (error) {
        setStatus($("editor-status"), `删除失败：${error.message}`, true);
      }
    });
    actions.append(editButton, deleteButton);
    row.append(details, actions);
    list.append(row);
  });
  renderAdminPagination(pageCount);
}

function adminPaginationPages(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const ordered = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const result = [];
  ordered.forEach((page, index) => {
    if (index && page - ordered[index - 1] > 1) result.push(null);
    result.push(page);
  });
  return result;
}

function renderAdminPagination(pageCount) {
  const navigation = $("admin-pagination");
  navigation.hidden = state.mods.length <= ADMIN_PAGE_SIZE;
  navigation.replaceChildren();
  if (navigation.hidden) return;

  const button = (label, page, options = {}) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `page-button${options.current ? " is-current" : ""}`;
    item.textContent = label;
    item.disabled = Boolean(options.disabled);
    if (options.current) item.setAttribute("aria-current", "page");
    item.addEventListener("click", () => {
      state.adminPage = page;
      renderMods();
      $("admin-count").scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return item;
  };

  navigation.append(button("上一页", state.adminPage - 1, { disabled: state.adminPage === 1 }));
  adminPaginationPages(state.adminPage, pageCount).forEach((page) => {
    if (page === null) {
      const gap = document.createElement("span");
      gap.className = "page-gap";
      gap.textContent = "…";
      navigation.append(gap);
    } else {
      navigation.append(button(String(page), page, { current: page === state.adminPage }));
    }
  });
  navigation.append(button("下一页", state.adminPage + 1, { disabled: state.adminPage === pageCount }));
}

async function loadMods() {
  state.mods = await api("/api/v1/admin/mods");
  renderMods();
}

function formatSubmittedAt(value) {
  const date = new Date(Number(value || 0) * 1000);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { hour12: false });
}

function renderSubmissions() {
  const list = $("submission-list");
  list.replaceChildren();
  $("submission-count").textContent = `${state.submissions.length} 项`;
  if (state.submissions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-admin-list";
    empty.textContent = "目前没有待审核投稿。";
    list.append(empty);
    return;
  }
  state.submissions.forEach((submission) => {
    const row = document.createElement("article");
    row.className = "admin-row";
    const details = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = submission.name;
    const meta = document.createElement("p");
    meta.textContent = `${submission.target_mod_id ? "修改申请" : "新增投稿"} · ${submission.mod_type === "map" ? "地图" : "工具"} · ${submission.author || "未填写作者"} · ${formatSubmittedAt(submission.submitted_at)}`;
    details.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "admin-actions";
    const reviewButton = document.createElement("button");
    reviewButton.className = "row-button";
    reviewButton.type = "button";
    reviewButton.textContent = "审核";
    reviewButton.addEventListener("click", () => review(submission));
    const rejectButton = document.createElement("button");
    rejectButton.className = "row-button danger-button";
    rejectButton.type = "button";
    rejectButton.textContent = "拒绝";
    rejectButton.addEventListener("click", async () => {
      if (!confirm(`确定拒绝“${submission.name}”吗？`)) return;
      try {
        await api(`/api/v1/admin/submissions/${submission.id}/reject`, { method: "POST" });
        if ($("submission-id").value === String(submission.id)) resetEditor();
        await loadSubmissions();
      } catch (error) {
        setStatus($("editor-status"), `拒绝失败：${error.message}`, true);
      }
    });
    actions.append(reviewButton, rejectButton);
    row.append(details, actions);
    list.append(row);
  });
}

async function loadSubmissions() {
  state.submissions = await api("/api/v1/admin/submissions");
  renderSubmissions();
}

function payload() {
  const isMap = $("field-mod-type").value === "map";
  return {
    name: $("field-name").value,
    author: $("field-author").value,
    version: $("field-version").value,
    level_key: isMap ? $("field-level-key").value : "",
    level_set_uid: isMap ? $("field-level-set-uid").value : "",
    scene_name: isMap ? $("field-scene-name").value : "",
    mod_type: $("field-mod-type").value,
    description: $("field-description").value,
    download_label: $("field-download-label").value || "项目页面",
    download_url: $("field-download-url").value,
    download_instructions: $("field-download-instructions").value,
    enabled: $("field-enabled").checked,
    featured: $("field-featured").checked,
  };
}

function updateIdentityFields() {
  const isMap = $("field-mod-type").value === "map";
  document.querySelectorAll(".map-identity").forEach((label) => {
    const input = label.querySelector("input");
    input.disabled = !isMap;
    label.classList.toggle("is-disabled", !isMap);
  });
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.token = $("admin-token").value.trim();
  sessionStorage.setItem("overmod-admin-token", state.token);
  setStatus($("login-status"), "正在验证……");
  await verify();
});

$("admin-logout").addEventListener("click", () => {
  state.token = "";
  sessionStorage.removeItem("overmod-admin-token");
  $("admin-token").value = "";
  showWorkspace(false);
});

$("edit-cancel").addEventListener("click", resetEditor);
$("field-mod-type").addEventListener("change", updateIdentityFields);
["field-level-set-uid", "field-scene-name"].forEach((id) => {
  $(id).addEventListener("input", () => {
    if ($("field-level-set-uid").value.trim() && $("field-scene-name").value.trim()) {
      $("field-level-key").value = "";
    }
  });
});
$("mod-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = $("mod-id").value;
  const submissionId = $("submission-id").value;
  setStatus($("editor-status"), "正在保存……");
  try {
    const path = submissionId
      ? `/api/v1/admin/submissions/${submissionId}/approve`
      : (id ? `/api/v1/admin/mods/${id}` : "/api/v1/admin/mods");
    await api(path, {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload()),
    });
    resetEditor();
    setStatus($("editor-status"), submissionId ? "投稿已通过并公开。" : "已保存。");
    await Promise.all([loadMods(), loadSubmissions()]);
  } catch (error) {
    setStatus($("editor-status"), `保存失败：${error.message}`, true);
  }
});

updateIdentityFields();
verify();
