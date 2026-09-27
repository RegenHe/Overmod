"use strict";

const state = {
  mods: [],
  popularLoaded: false,
  modSort: { key: "updated", direction: "desc" },
};
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
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

function showView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("is-visible"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("is-active"));
  $(`view-${name}`).classList.add("is-visible");
  document.querySelector(`.nav-item[data-view="${name}"]`).classList.add("is-active");
  history.replaceState(null, "", name === "popular" ? "/" : `/#${name}`);
  if (name === "popular" && !state.popularLoaded) loadPopular();
}

function shortKey(levelKey) {
  const compact = String(levelKey || "").replace(/[^a-zA-Z0-9]/g, "");
  return (compact.slice(-6) || "------").toUpperCase();
}

function modKey(id) {
  return `M${String(id || 0).padStart(5, "0")}`;
}

function catalogueKey(mod) {
  return mod.mod_type === "map" && mod.level_key ? shortKey(mod.level_key) : modKey(mod.id);
}

function labelPrefix(label) {
  const parts = String(label || "").split("/");
  return parts.length > 1 ? parts[0].trim() : "—";
}

function formatTime(value) {
  const seconds = Number(value || 0);
  if (!seconds) return "—";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  const now = new Date();
  const sameYear = date.getFullYear() === now.getFullYear();
  return new Intl.DateTimeFormat("zh-CN", {
    year: sameYear ? undefined : "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date).replaceAll("/", "-");
}

function formatDatabaseTime(value) {
  if (!value) return "—";
  const iso = String(value).includes("T") ? String(value) : `${value.replace(" ", "T")}Z`;
  const milliseconds = Date.parse(iso);
  return Number.isNaN(milliseconds) ? String(value) : formatTime(milliseconds / 1000);
}

function cell(text, className = "") {
  const node = document.createElement("td");
  node.textContent = text;
  if (className) node.className = className;
  node.title = text;
  return node;
}

function typeCell(type) {
  const node = document.createElement("td");
  node.className = "type-cell";
  const badge = document.createElement("span");
  badge.className = `type-badge is-${type}`;
  badge.textContent = type === "map" ? "地图" : "工具";
  node.append(badge);
  return node;
}

function detailPair(label, value) {
  const item = document.createElement("div");
  item.className = "detail-pair";
  const caption = document.createElement("span");
  caption.textContent = label;
  const content = document.createElement("strong");
  content.textContent = value || "—";
  item.append(caption, content);
  return item;
}

function detailRow(columnCount, options) {
  const row = document.createElement("tr");
  row.className = "detail-row";
  row.hidden = true;
  const holder = document.createElement("td");
  holder.colSpan = columnCount;
  const panel = document.createElement("div");
  panel.className = "row-details";
  const facts = document.createElement("div");
  facts.className = "detail-facts";
  (options.facts || []).forEach(([label, value]) => facts.append(detailPair(label, value)));
  const description = document.createElement("p");
  description.className = "detail-description";
  description.textContent = options.description || "暂无详细介绍。";
  panel.append(facts, description);
  if (options.instructions) {
    const instructions = document.createElement("p");
    instructions.className = "detail-instructions";
    instructions.textContent = options.instructions;
    panel.append(instructions);
  }
  if (options.url) {
    const link = document.createElement("a");
    link.className = "download-link detail-download";
    link.href = options.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer nofollow";
    link.textContent = options.linkLabel || "下载页面";
    link.addEventListener("click", (event) => event.stopPropagation());
    panel.append(link);
  } else {
    const unavailable = document.createElement("span");
    unavailable.className = "download-unavailable";
    unavailable.textContent = "暂未收录下载方式";
    panel.append(unavailable);
  }
  holder.append(panel);
  row.append(holder);
  return row;
}

function makeExpandable(row, details) {
  row.classList.add("expandable-row");
  row.tabIndex = 0;
  row.setAttribute("aria-expanded", "false");
  const toggle = () => {
    const opened = !details.hidden;
    details.hidden = opened;
    row.classList.toggle("is-open", !opened);
    row.setAttribute("aria-expanded", String(!opened));
  };
  row.addEventListener("click", toggle);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });
}

function popularRow(entry) {
  const row = document.createElement("tr");
  const metadata = entry.catalogue || {};
  const rank = cell(String(entry.rank || "—"), "rank-cell");
  const key = cell(shortKey(entry.level_key), "key-cell");
  key.title = entry.level_key || "";
  const title = cell(entry.level_label || entry.level_name || entry.level_key || "未知关卡", "title-cell");
  const author = cell(metadata.author || labelPrefix(entry.level_label), "author-cell");
  const version = cell(metadata.version || "—", "version-cell");
  const count = cell(String(entry.play_count || 0), "count-cell");
  row.append(rank, key, title, author, version, count);
  const details = detailRow(6, {
    facts: [
      ["完整关卡键", entry.level_key],
      ["内部名称", entry.level_name],
      ["最高分", `${entry.top_score || 0} · ${entry.top_score_player || "—"}`],
      ["最多菜", `${entry.top_dishes || 0} · ${entry.top_dishes_player || "—"}`],
    ],
    description: metadata.description || "该关卡尚未在 Overmod 收录详细介绍。",
    instructions: metadata.download_instructions || "",
    url: metadata.download_url || "",
    linkLabel: metadata.download_label || "下载页面",
  });
  makeExpandable(row, details);
  return [row, details];
}

async function loadPopular() {
  setStatus($("popular-status"), "正在读取热门数据……");
  try {
    const result = await api("/api/v1/popular-levels");
    const entries = result.entries || [];
    const list = $("popular-list");
    list.replaceChildren(...entries.flatMap(popularRow));
    state.popularLoaded = true;
    setStatus(
      $("popular-status"),
      entries.length
        ? `${entries.length} 个热门自定义关卡${result.stale ? " · 当前为缓存数据" : ""}`
        : "最近 7 天还没有自定义关卡记录。",
    );
  } catch (error) {
    setStatus($("popular-status"), `热门数据暂不可用：${error.message}`, true);
  }
}

function renderMods() {
  const query = $("mod-search").value.trim().toLocaleLowerCase();
  const matches = state.mods.filter((mod) =>
    [mod.name, mod.author, mod.description, mod.mod_type === "map" ? "地图" : "工具"]
      .join(" ").toLocaleLowerCase().includes(query)
  );
  const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
  const sort = state.modSort.key;
  const direction = state.modSort.direction === "asc" ? 1 : -1;
  matches.sort((left, right) => {
    let compared = 0;
    if (sort === "key") compared = collator.compare(catalogueKey(left), catalogueKey(right));
    else if (sort === "name") compared = collator.compare(left.name || "", right.name || "");
    else if (sort === "author") compared = collator.compare(left.author || "", right.author || "");
    else if (sort === "version") compared = collator.compare(left.version || "", right.version || "");
    else if (sort === "type") compared = collator.compare(left.mod_type || "", right.mod_type || "");
    else compared = String(left.updated_at || "").localeCompare(String(right.updated_at || ""));
    return compared === 0 ? collator.compare(left.name || "", right.name || "") : compared * direction;
  });
  const list = $("mod-list");
  list.replaceChildren();
  matches.forEach((mod) => {
    const row = document.createElement("tr");
    row.append(
      cell(catalogueKey(mod), "key-cell"),
      cell(mod.name, "title-cell"),
      typeCell(mod.mod_type),
      cell(mod.author || "—", "author-cell"),
      cell(mod.version || "—", "version-cell"),
      cell(formatDatabaseTime(mod.updated_at), "time-cell"),
    );
    const facts = [
        ["模组类型", mod.mod_type === "map" ? "地图" : "工具"],
        ["作者", mod.author || "—"],
        ["版本", mod.version || "—"],
    ];
    if (mod.mod_type === "map") {
      facts.push(
        ["关联关卡", mod.level_key || "—"],
        ["合集 UID", mod.level_set_uid || "—"],
        ["sceneName", mod.scene_name || "—"],
      );
    }
    const details = detailRow(6, {
      facts,
      description: mod.description || (mod.mod_type === "map" ? "该地图由 Overrank 玩局记录自动发现，尚未收录详细介绍。" : ""),
      instructions: mod.download_instructions || "请在作者页面查看下载与安装说明。",
      url: mod.download_url,
      linkLabel: mod.download_label || "项目页面",
    });
    makeExpandable(row, details);
    list.append(row, details);
  });
  const maps = matches.filter((mod) => mod.mod_type === "map").length;
  const tools = matches.length - maps;
  setStatus($("mod-status"), matches.length ? `${matches.length} 个模组 · ${maps} 个地图 · ${tools} 个工具` : (query ? "没有匹配的模组。" : "目录中还没有模组。"));
}

function updateSortHeaders() {
  document.querySelectorAll(".mod-table .sort-button").forEach((button) => {
    const active = button.dataset.sort === state.modSort.key;
    const header = button.closest("th");
    button.classList.toggle("is-active", active);
    if (active) {
      button.dataset.direction = state.modSort.direction;
      header.setAttribute("aria-sort", state.modSort.direction === "asc" ? "ascending" : "descending");
    } else {
      delete button.dataset.direction;
      header.removeAttribute("aria-sort");
    }
  });
}

function changeModSort(key) {
  if (state.modSort.key === key) {
    state.modSort.direction = state.modSort.direction === "asc" ? "desc" : "asc";
  } else {
    state.modSort.key = key;
    state.modSort.direction = key === "updated" ? "desc" : "asc";
  }
  updateSortHeaders();
  renderMods();
}

function updateSubmissionIdentityFields() {
  const isMap = $("submit-mod-type").value === "map";
  document.querySelectorAll(".submit-map-identity").forEach((label) => {
    const input = label.querySelector("input");
    input.disabled = !isMap;
    label.classList.toggle("is-disabled", !isMap);
  });
}

function submissionPayload() {
  const isMap = $("submit-mod-type").value === "map";
  return {
    name: $("submit-name").value,
    author: $("submit-author").value,
    version: $("submit-version").value,
    level_key: isMap ? $("submit-level-key").value : "",
    level_set_uid: isMap ? $("submit-level-set-uid").value : "",
    scene_name: isMap ? $("submit-scene-name").value : "",
    mod_type: $("submit-mod-type").value,
    description: $("submit-description").value,
    download_label: $("submit-download-label").value || "项目页面",
    download_url: $("submit-download-url").value,
    download_instructions: $("submit-download-instructions").value,
  };
}

async function submitCatalogueEntry(event) {
  event.preventDefault();
  const button = $("submission-submit");
  button.disabled = true;
  setStatus($("submission-status"), "正在提交……");
  try {
    await api("/api/v1/submissions", {
      method: "POST",
      body: JSON.stringify(submissionPayload()),
    });
    $("submission-form").reset();
    $("submit-download-label").value = "项目页面";
    updateSubmissionIdentityFields();
    setStatus($("submission-status"), "已提交，审核通过后会出现在模组目录中。");
  } catch (error) {
    setStatus($("submission-status"), `提交失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

async function loadMods() {
  try {
    state.mods = await api("/api/v1/mods");
    renderMods();
  } catch (error) {
    setStatus($("mod-status"), `目录加载失败：${error.message}`, true);
  }
}

document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
$("popular-refresh").addEventListener("click", loadPopular);
$("mod-search").addEventListener("input", renderMods);
document.querySelectorAll(".mod-table .sort-button").forEach((button) => {
  button.addEventListener("click", () => changeModSort(button.dataset.sort));
});
updateSortHeaders();
$("submit-mod-type").addEventListener("change", updateSubmissionIdentityFields);
["submit-level-set-uid", "submit-scene-name"].forEach((id) => {
  $(id).addEventListener("input", () => {
    if ($("submit-level-set-uid").value.trim() && $("submit-scene-name").value.trim()) {
      $("submit-level-key").value = "";
    }
  });
});
$("submission-form").addEventListener("submit", submitCatalogueEntry);
updateSubmissionIdentityFields();
const initialView = location.hash === "#mods"
  ? "mods"
  : (location.hash === "#submit" ? "submit" : "popular");
showView(initialView);
loadMods();
