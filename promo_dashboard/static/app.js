"use strict";

const $ = (selector) => document.querySelector(selector);
const activeStates = new Set(["starting", "running", "pausing", "paused", "stopping"]);
const modeNames = { scan: "已知码扫描", discover: "候选发现", matrix: "矩阵发现" };
const stateNames = {
  starting: "准备中", running: "执行中", pausing: "等待暂停", paused: "已暂停",
  stopping: "正在停止", cancelled: "已停止", completed: "已完成", failed: "执行失败",
  interrupted: "服务中断", ELIGIBLE: "可用", EXISTS: "存在", NOT_FOUND: "未找到",
  ERROR: "异常", SKIPPED: "已跳过", PREVIEW: "待验证", REFERENCE: "历史有效 · 未验证",
  EXPIRED: "历史已失效",
};
const routeInfo = {
  workspace: ["扫描工作台", "从候选到结果，每一步都看得见。"],
  history: ["任务记录", "每一次探索，都有据可查。"],
  catalog: ["已知码库", "从仓库的历史发现中，找到下一次扫描的起点。"],
  settings: ["连接设置", "配置一次，然后在工作台专注执行。"],
};
const app = {
  settings: null, regions: [], catalog: null, jobs: [], job: null, page: 1,
  resultTotal: 0, lastLog: 0, logCount: 0, resultVersion: "",
  ready: false, pending: false, pollTimer: null, toastTimer: null, resultsRequest: 0, confirmDelete: null,
};
const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  hour12: false,
});
const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("class", "icon");
  node.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  node.append(use);
  return node;
}

function tone(status) {
  if (["completed", "ELIGIBLE", "running"].includes(status)) return "success";
  if (["failed", "ERROR", "EXPIRED", "interrupted"].includes(status)) return "error";
  if (["EXISTS", "pausing", "paused", "stopping", "starting"].includes(status)) return "warning";
  return "neutral";
}

function badge(status) { return element("span", stateNames[status] || status, `badge ${tone(status)}`); }
function regionName(code) {
  if (code === "ALL") return "所有地区";
  const region = app.regions.find((item) => item.code === code);
  return region ? `${region.name} · ${code}` : code;
}

function jobTitle(job) {
  return `${job.request.preview ? "候选预览" : modeNames[job.request.mode]} · ${regionName(job.request.region)}`;
}

function elapsed(job) {
  const seconds = Math.max(0, Math.floor((Date.parse(job.ended_at || new Date().toISOString()) -
    Date.parse(job.started_at)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function showError(selector, message) {
  const node = $(selector);
  node.textContent = message || "";
  node.hidden = !message;
}

function toast(message) {
  clearTimeout(app.toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  app.toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store", ...options,
    headers: { "Content-Type": "application/json", "X-Promo-Panel": "1", ...options.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    let detail = payload.detail || `请求失败（HTTP ${response.status}）`;
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg.replace(/^Value error, /, "")).join("；");
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function connected(ok) {
  $("#connection-label").textContent = ok ? "面板已连接" : "面板连接中断";
  $("#connection-dot").classList.toggle("offline", !ok);
  $("#page-error").hidden = ok;
  if (!ok) $("#page-error-text").textContent = "暂时无法连接服务。请确认面板服务仍在运行，页面会自动重试。";
}

function renderRoute() {
  let route = location.hash.slice(1) || "workspace";
  if (!routeInfo[route]) route = "workspace";
  Object.keys(routeInfo).forEach((name) => {
    $(`#page-${name}`).hidden = !app.ready || name !== route;
    const link = $(`[data-route="${name}"]`);
    if (name === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("#page-title").textContent = routeInfo[route][0];
  $("#page-description").textContent = routeInfo[route][1];
  document.title = `Promo Scanner · ${routeInfo[route][0]}`;
  if (app.ready && route === "history") renderHistory();
  if (app.ready && route === "catalog") renderCatalog();
}

function populateRegions(selector, allLabel) {
  const select = $(selector);
  select.replaceChildren(new Option(allLabel, ["#catalog-region", "#result-region"].includes(selector) ? "" : "ALL"));
  for (const region of app.regions) select.add(new Option(`${region.name} · ${region.code}`, region.code));
}

const settingFields = {
  proxy_url: "proxy", clash_transport: "transport", clash_socket: "socket", clash_url: "clash-url",
  rule_group: "rule-group", global_group: "global-group", auto_switch: "auto-switch",
  request_delay: "delay", request_timeout: "timeout", default_mode: "mode", default_region: "region",
  collect_price: "price", use_known_bases: "bases", include_company_lists: "companies",
};

function fillSettings() {
  const settings = app.settings;
  for (const [name, id] of Object.entries(settingFields)) {
    const input = $(`#setting-${id}`);
    if (input.type === "checkbox") input.checked = settings[name];
    else input.value = settings[name];
  }
  $("#setting-codes").value = settings.custom_codes.join("\n");
  $("#setting-custom-bases").value = settings.custom_bases.join("\n");
  $("#setting-token").value = "";
  $("#setting-clash-secret").value = "";
  $("#clear-token").checked = false;
  $("#clear-clash-secret").checked = false;
  $("#token-status").textContent = settings.token_configured ? "已保存 · 留空保留" : "尚未配置";
  $("#clash-secret-status").textContent = settings.clash_secret_configured ? "已保存 · 留空保留" : "未配置（可选）";
  const keywords = $("#keyword-fields");
  keywords.replaceChildren();
  for (const region of app.regions) {
    const field = element("div", null, "field");
    const label = element("label", `${region.name} · ${region.code}`);
    label.htmlFor = `keyword-${region.code}`;
    const input = element("input");
    input.id = `keyword-${region.code}`;
    input.dataset.region = region.code;
    input.value = (settings.region_keywords[region.code] || []).join(", ");
    input.placeholder = "使用内置匹配";
    field.append(label, input);
    keywords.append(field);
  }
  $("#settings-saved-state").textContent = "已保存";
  $("#settings-saved-state").className = "badge success";
  $("#settings-footnote").textContent = "所有配置保存在本机工作空间。";
  toggleTransport();
  updateConfigurationState();
}

function toggleTransport() {
  const unix = $("#setting-transport").value === "unix";
  $("#socket-field").hidden = !unix;
  $("#clash-url-field").hidden = unix;
}

function updateConfigurationState() {
  $("#setup-notice").hidden = app.settings.token_configured;
  $("#settings-dot").hidden = app.settings.token_configured;
  $("#task-delay").textContent = `请求间隔 ${app.settings.request_delay}s`;
  updateMode();
}

function updateMode() {
  const mode = $("#job-mode").value;
  const explanations = {
    discover: "使用基础名与国家后缀生成候选，验证是否存在。",
    scan: "验证内置和自定义码，并生成可用的结账链接。",
    matrix: "交叉组合公司名与地区，同时尝试无后缀码。",
  };
  $("#mode-help").textContent = explanations[mode];
  $("#job-price-row").hidden = mode !== "scan";
  $("#region-help").textContent = mode === "scan" && app.settings?.auto_switch ?
    "按地区匹配 Clash 节点；未找到节点的候选会标为已跳过。" :
    "发现任务使用当前出站连接，所选地区用于生成候选后缀。";
  if (mode === "scan" && !app.settings?.auto_switch) {
    $("#region-help").textContent = "使用当前出站连接验证所选地区；自动切换节点已关闭。";
  }
  const selected = app.regions.find((item) => item.code === $("#job-region").value);
  if (mode === "scan" && selected && !selected.scan_supported) {
    $("#region-help").textContent = "该地区暂无内置码，请先在连接设置中添加自定义候选码。";
  }
  renderControls();
}

function renderControls() {
  const hasActive = app.jobs.some((job) => activeStates.has(job.status));
  $("#start-button").disabled = hasActive || app.pending;
  $("#preview-button").disabled = hasActive || app.pending;
  $("#start-label").textContent = app.pending ? "提交中…" :
    (!app.settings?.token_configured ? "配置连接" : "开始任务");
  const status = app.job?.status;
  $("#pause-button").hidden = !["starting", "running"].includes(status);
  $("#resume-button").hidden = !["paused", "pausing"].includes(status);
  $("#stop-button").hidden = !status || !activeStates.has(status);
  $("#stop-button").disabled = status === "stopping" || app.pending;
  $("#pause-button").disabled = app.pending;
  $("#resume-button").disabled = app.pending;
}

function renderJob() {
  const job = app.job;
  $("#run-empty").hidden = !!job;
  $("#run-details").hidden = !job;
  if (job) {
    $("#run-status").textContent = stateNames[job.status] || job.status;
    $("#run-status").className = `badge ${tone(job.status)}`;
    $("#run-title").textContent = jobTitle(job);
    $("#run-id").textContent = `#${job.id.slice(0, 8)}`;
    $("#run-current").textContent = job.current || "准备执行";
    $("#run-progress-text").textContent = `${job.done} / ${job.total}`;
    $("#run-progress").max = job.total || 1;
    $("#run-progress").value = job.done;
    $("#stat-eligible").textContent = job.counts.eligible;
    $("#stat-exists").textContent = job.counts.exists;
    $("#stat-errors").textContent = `${job.counts.error} / ${job.counts.skipped}`;
    $("#stat-elapsed").textContent = elapsed(job);
    $("#run-note").textContent = job.request.preview ? "预览任务 · 未调用外部 API" :
      job.status === "pausing" ? "当前请求结束后暂停" :
      job.status === "stopping" ? "正在结束请求并清理任务" :
      job.status === "paused" ? "暂停后不会发出新请求" :
      activeStates.has(job.status) ? "正在使用本次任务的配置快照" : "结果与日志已保留";
    $("#results-subtitle").textContent = `${jobTitle(job)} · ${job.request.preview ? "候选预览，尚未验证" : "执行结果实时更新"}`;
    showError("#run-error", job.error);
  } else {
    $("#run-status").textContent = "等待任务";
    $("#run-status").className = "badge neutral";
  }
  $("#history-count").textContent = app.jobs.length;
  $("#history-count").hidden = app.jobs.length === 0;
  renderControls();
}

function selectJob(job) {
  app.job = job;
  app.page = 1;
  app.lastLog = 0;
  app.logCount = 0;
  app.resultVersion = "";
  $("#log-stream").replaceChildren(element("p", "正在读取任务日志…", "log-placeholder"));
  $("#log-count").textContent = "0";
  $("#result-search").value = "";
  $("#result-status").value = "";
  $("#result-region").value = "";
  renderJob();
  loadResults().catch((error) => toast(error.message));
  loadLogs().catch((error) => toast(error.message));
}

async function startJob(preview) {
  if (app.pending) return;
  if (!preview && !app.settings.token_configured) {
    location.hash = "settings";
    $("#setting-token").focus();
    return;
  }
  app.pending = true;
  showError("#job-form-error", "");
  renderControls();
  try {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({
      mode: $("#job-mode").value, region: $("#job-region").value,
      collect_price: $("#job-price").checked && $("#job-mode").value === "scan", preview,
    }) });
    app.jobs.unshift(job);
    selectJob(job);
    toast(preview ? "候选预览已启动" : "任务已启动，进度将实时更新");
  } catch (error) { showError("#job-form-error", error.message); }
  finally { app.pending = false; renderControls(); }
}

async function controlJob(action) {
  if (!app.job || app.pending) return;
  app.pending = true;
  renderControls();
  try {
    app.job = await api(`/api/jobs/${app.job.id}/control`, {
      method: "POST", body: JSON.stringify({ action }),
    });
    const index = app.jobs.findIndex((job) => job.id === app.job.id);
    if (index >= 0) app.jobs[index] = app.job;
    renderJob();
    toast({ pause: "已请求暂停，当前请求结束后生效", resume: "任务已继续", stop: "正在停止任务，已有结果会保留" }[action]);
  } catch (error) { toast(error.message); }
  finally { app.pending = false; renderControls(); }
}

function resultQuery() {
  return new URLSearchParams({ q: $("#result-search").value,
    status: $("#result-status").value, region: $("#result-region").value, page: app.page, per_page: 50 });
}

async function copyCode(code) {
  try { await navigator.clipboard.writeText(code); toast(`已复制 ${code}`); }
  catch { toast("复制失败，请在表格中选中促销码后复制。"); }
}

function copyButton(code) {
  const button = element("button", null, "icon-button");
  button.type = "button";
  button.append(icon("copy"));
  button.title = `复制 ${code}`;
  button.setAttribute("aria-label", button.title);
  button.addEventListener("click", () => copyCode(code));
  return button;
}

function priceText(row) {
  const price = row.price_info;
  if (price?.actual_price !== undefined) {
    return `${price.discount_currency || row.currency} ${Number(price.actual_price).toFixed(2)}`;
  }
  return "—";
}

async function loadResults() {
  if (!app.job) return;
  const requestNumber = ++app.resultsRequest;
  const jobId = app.job.id;
  const data = await api(`/api/jobs/${jobId}/results?${resultQuery()}`);
  if (requestNumber !== app.resultsRequest || jobId !== app.job?.id) return;
  app.resultTotal = data.total;
  const maxPage = Math.max(1, Math.ceil(data.total / 50));
  if (app.page > maxPage) { app.page = maxPage; return loadResults(); }
  const body = $("#result-body");
  body.replaceChildren();
  for (const row of data.results) {
    const tr = element("tr");
    const code = element("td", row.code, "code-cell");
    const region = element("td", row.region);
    const status = element("td"); status.append(badge(row.status));
    const price = element("td", priceText(row));
    if (row.price_info?.actual_usd != null) {
      price.append(element("span", `≈ $${row.price_info.actual_usd}`, "subline"));
    }
    const detail = element("td", row.status === "PREVIEW" ? "未请求 · 仅生成候选" :
      (row.detail || (row.stripe_url ? "已生成结账链接" : "—")), "detail-cell");
    const actions = element("td");
    const actionsGroup = element("div", null, "table-actions");
    actionsGroup.append(copyButton(row.code));
    if (row.stripe_url) {
      try {
        const url = new URL(row.stripe_url);
        if (url.protocol === "https:") {
          const link = element("a", "查看结账", "btn small");
          link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
          link.append(icon("link")); actionsGroup.append(link);
        }
      } catch { /* Invalid links remain unavailable. */ }
    }
    actions.append(actionsGroup);
    tr.append(code, region, status, price, detail, actions);
    body.append(tr);
  }
  $("#results-empty").hidden = data.results.length > 0;
  if (!data.results.length) {
    $("#results-empty h3").textContent = app.job.done ? "没有符合筛选条件的结果" : "等待第一条执行结果";
    $("#results-empty p").textContent = app.job.done ? "修改搜索词或状态筛选，查看其他结果。" :
      app.job.error ? "任务未产生结果，请查看执行状态与运行日志。" : "任务执行中，结果会自动出现在这里。";
  }
  $("#result-pagination").hidden = data.total === 0;
  $("#result-total").textContent = `共 ${data.total} 条${app.job.request.preview ? "候选" : "结果"}`;
  $("#page-number").textContent = `${app.page} / ${maxPage}`;
  $("#previous-page").disabled = app.page <= 1;
  $("#next-page").disabled = app.page >= maxPage;
  $("#export-json").disabled = data.total === 0;
  $("#export-csv").disabled = data.total === 0;
}

async function loadLogs() {
  if (!app.job) return;
  const jobId = app.job.id;
  const data = await api(`/api/jobs/${jobId}/logs?after=${app.lastLog}`);
  if (app.job?.id !== jobId) return;
  const stream = $("#log-stream");
  if (data.logs.length && app.lastLog === 0) stream.replaceChildren();
  for (const log of data.logs) {
    if (log.id <= app.lastLog) continue;
    const line = element("div", null, "log-line");
    const time = element("time", timeFormatter.format(new Date(log.time)));
    time.dateTime = log.time;
    line.append(time, element("span", log.message));
    stream.append(line);
    app.lastLog = log.id;
    app.logCount++;
  }
  while (stream.children.length > 1000) stream.firstElementChild.remove();
  $("#log-count").textContent = app.logCount;
  if (!app.logCount && !activeStates.has(app.job.status)) {
    stream.replaceChildren(element("p", "此任务没有产生运行日志。", "log-placeholder"));
  }
  if (data.logs.length && $("#follow-logs").checked) stream.scrollTop = stream.scrollHeight;
}

function selectTab(name) {
  ["results", "logs"].forEach((tab) => {
    const selected = tab === name;
    $(`#tab-${tab}`).setAttribute("aria-selected", String(selected));
    $(`#tab-${tab}`).tabIndex = selected ? 0 : -1;
  });
  $("#result-view").hidden = name !== "results";
  $("#log-view").hidden = name !== "logs";
  if (name === "logs" && $("#follow-logs").checked) {
    $("#log-stream").scrollTop = $("#log-stream").scrollHeight;
  }
}

function renderHistory() {
  const body = $("#history-body");
  body.replaceChildren();
  $("#history-empty").hidden = app.jobs.length > 0;
  for (const job of app.jobs) {
    const row = element("tr");
    const title = element("td", jobTitle(job));
    title.append(element("span", `#${job.id.slice(0, 8)} · ${elapsed(job)}`, "subline"));
    const status = element("td"); status.append(badge(job.status));
    const progress = element("td", `${job.done} / ${job.total}`);
    const found = element("td", `${job.counts.eligible} / ${job.counts.exists}`);
    const date = element("td", dateFormatter.format(new Date(job.created_at)));
    const actions = element("td");
    const group = element("div", null, "table-actions");
    const view = element("button", "查看", "btn small");
    view.addEventListener("click", () => { selectJob(job); location.hash = "workspace"; });
    group.append(view);
    if (!activeStates.has(job.status)) {
      const remove = element("button", app.confirmDelete === job.id ? "确认删除" : "删除", "btn small danger");
      remove.addEventListener("click", async () => {
        if (app.confirmDelete !== job.id) { app.confirmDelete = job.id; renderHistory(); return; }
        remove.disabled = true;
        try {
          await api(`/api/jobs/${job.id}`, { method: "DELETE" });
          app.jobs = app.jobs.filter((item) => item.id !== job.id);
          app.confirmDelete = null;
          if (app.job?.id === job.id) {
            app.job = null;
            if (app.jobs.length) selectJob(app.jobs[0]);
            else resetWorkspace();
          }
          renderHistory(); renderJob(); toast("任务记录已删除");
        } catch (error) { remove.disabled = false; toast(error.message); }
      });
      group.append(remove);
      if (app.confirmDelete === job.id) {
        const cancel = element("button", "取消", "btn small");
        cancel.addEventListener("click", () => { app.confirmDelete = null; renderHistory(); });
        group.append(cancel);
      }
    }
    actions.append(group);
    row.append(title, status, progress, found, date, actions);
    body.append(row);
  }
}

function resetWorkspace() {
  app.resultsRequest++;
  app.lastLog = 0; app.logCount = 0;
  $("#result-body").replaceChildren();
  $("#results-empty").hidden = false;
  $("#results-empty h3").textContent = "让第一份结果出现在这里";
  $("#results-empty p").textContent = "选择任务与地区，点击「预览候选」查看待验证列表。";
  $("#result-pagination").hidden = true;
  $("#export-json").disabled = true; $("#export-csv").disabled = true;
  $("#log-stream").replaceChildren(element("p", "任务开始后，运行日志将显示在这里。", "log-placeholder"));
  $("#log-count").textContent = "0";
  $("#results-subtitle").textContent = "任务执行后，结果会保留在任务记录中。";
}

function renderCatalog() {
  const query = $("#catalog-search").value.toLowerCase();
  const region = $("#catalog-region").value;
  const rows = app.catalog.results.filter((row) =>
    (!region || row.region === region) && (!query || `${row.code} ${row.company || ""}`.toLowerCase().includes(query)));
  const body = $("#catalog-body");
  body.replaceChildren();
  for (const row of rows) {
    const tr = element("tr");
    const code = element("td", row.code, "code-cell");
    code.append(element("span", row.company || "", "subline"));
    const status = element("td"); status.append(badge(row.status));
    const price = element("td", row.price_usd != null ? `$${row.price_usd}` : "—");
    if (row.price_local) price.append(element("span", row.price_local, "subline"));
    const actions = element("td");
    const group = element("div", null, "table-actions");
    group.append(copyButton(row.code)); actions.append(group);
    tr.append(code, element("td", row.region), status, price,
      element("td", row.discount_pct != null ? `${row.discount_pct}%` : "—"), actions);
    body.append(tr);
  }
  $("#catalog-count").textContent = `${rows.length} 条参考记录`;
  $("#catalog-empty").hidden = rows.length > 0;
  $("#catalog-notice").textContent = `仓库参考数据 · 更新于 ${app.catalog.updated_at}。历史有效性和价格尚未重新验证，请通过扫描确认。`;
}

async function saveSettings(event) {
  event.preventDefault();
  const button = $("#save-settings");
  button.disabled = true;
  showError("#settings-error", "");
  try {
    const data = {};
    for (const [name, id] of Object.entries(settingFields)) {
      const input = $(`#setting-${id}`);
      data[name] = input.type === "checkbox" ? input.checked :
        input.type === "number" ? Number(input.value) : input.value;
    }
    data.token = $("#setting-token").value.trim();
    data.clash_secret = $("#setting-clash-secret").value.trim();
    data.clear_token = $("#clear-token").checked;
    data.clear_clash_secret = $("#clear-clash-secret").checked;
    data.custom_codes = $("#setting-codes").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    data.custom_bases = $("#setting-custom-bases").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    data.region_keywords = {};
    $("#keyword-fields").querySelectorAll("input").forEach((input) => {
      const keywords = input.value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
      if (keywords.length) data.region_keywords[input.dataset.region] = keywords;
    });
    app.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(data) });
    fillSettings();
    $("#job-mode").value = app.settings.default_mode;
    $("#job-region").value = app.settings.default_region;
    $("#job-price").checked = app.settings.collect_price;
    updateMode();
    toast("设置已保存，新任务将使用这份配置");
  } catch (error) { showError("#settings-error", error.message); }
  finally { button.disabled = false; }
}

async function checkConnections() {
  const button = $("#check-connections");
  const label = $("#connection-check-result");
  button.disabled = true;
  label.hidden = false; label.textContent = "正在检查已保存的连接…";
  try {
    const data = await api("/api/connections/check", { method: "POST" });
    label.textContent = `Token：${data.token.message}。Clash：${data.clash.message}。`;
  } catch (error) { label.textContent = error.message; }
  finally { button.disabled = false; }
}

async function poll() {
  clearTimeout(app.pollTimer);
  if (!app.ready) return;
  try {
    const data = await api("/api/jobs");
    app.jobs = data.jobs;
    if (app.job) {
      const latest = app.jobs.find((job) => job.id === app.job.id);
      if (latest) app.job = latest;
      else { app.job = null; resetWorkspace(); }
    } else if (app.jobs.length) selectJob(app.jobs[0]);
    renderJob();
    if (location.hash === "#history") renderHistory();
    if (app.job) {
      const version = `${app.job.id}:${app.job.done}:${app.job.status}`;
      if (version !== app.resultVersion) {
        await loadResults(); app.resultVersion = version;
      }
      await loadLogs();
    }
    connected(true);
  } catch { connected(false); }
  finally { app.pollTimer = setTimeout(poll, 1500); }
}

async function initialize() {
  clearTimeout(app.pollTimer);
  $("#loading").hidden = false;
  try {
    const data = await api("/api/bootstrap");
    Object.assign(app, { settings: data.settings, regions: data.regions,
      catalog: data.catalog, jobs: data.jobs, ready: true });
    populateRegions("#job-region", "所有地区");
    populateRegions("#setting-region", "所有地区");
    populateRegions("#catalog-region", "所有地区");
    populateRegions("#result-region", "所有地区");
    fillSettings();
    $("#job-mode").value = app.settings.default_mode;
    $("#job-region").value = app.settings.default_region;
    $("#job-price").checked = app.settings.collect_price;
    updateMode();
    const current = app.jobs.find((job) => activeStates.has(job.status)) || app.jobs[0];
    if (current) selectJob(current);
    else renderJob();
    renderRoute(); connected(true);
    app.pollTimer = setTimeout(poll, 1000);
  } catch (error) {
    connected(false);
    $("#page-error-text").textContent = `无法读取面板数据：${error.message}。请点击重新连接。`;
  } finally { $("#loading").hidden = true; }
}

window.addEventListener("hashchange", renderRoute);
$("#job-form").addEventListener("submit", (event) => { event.preventDefault(); startJob(false); });
$("#preview-button").addEventListener("click", () => startJob(true));
$("#job-mode").addEventListener("change", updateMode);
$("#job-region").addEventListener("change", updateMode);
$("#pause-button").addEventListener("click", () => controlJob("pause"));
$("#resume-button").addEventListener("click", () => controlJob("resume"));
$("#stop-button").addEventListener("click", () => controlJob("stop"));
$("#settings-form").addEventListener("submit", saveSettings);
$("#settings-form").addEventListener("input", () => {
  $("#settings-saved-state").textContent = "有未保存的更改";
  $("#settings-saved-state").className = "badge warning";
  $("#settings-footnote").textContent = "点击保存后，新任务才会使用更改。";
});
$("#setting-transport").addEventListener("change", toggleTransport);
$("#check-connections").addEventListener("click", checkConnections);
$("#retry-button").addEventListener("click", initialize);
$("#tab-results").addEventListener("click", () => selectTab("results"));
$("#tab-logs").addEventListener("click", () => selectTab("logs"));
$(".tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tab = event.key === "Home" ? "results" : event.key === "End" ? "logs" :
    $("#tab-results").getAttribute("aria-selected") === "true" ? "logs" : "results";
  selectTab(tab); $(`#tab-${tab}`).focus();
});
let searchTimer;
$("#result-search").addEventListener("input", () => {
  clearTimeout(searchTimer); searchTimer = setTimeout(() => {
    app.page = 1; loadResults().catch((error) => toast(error.message));
  }, 200);
});
$("#result-status").addEventListener("change", () => {
  app.page = 1; loadResults().catch((error) => toast(error.message));
});
$("#result-region").addEventListener("change", () => {
  app.page = 1; loadResults().catch((error) => toast(error.message));
});
$("#previous-page").addEventListener("click", () => { app.page--; loadResults().catch((error) => toast(error.message)); });
$("#next-page").addEventListener("click", () => { app.page++; loadResults().catch((error) => toast(error.message)); });
["json", "csv"].forEach((format) => $(`#export-${format}`).addEventListener("click", () => {
  if (!app.job) return;
  const query = resultQuery(); query.set("format", format);
  const link = element("a"); link.href = `/api/jobs/${app.job.id}/export?${query}`;
  link.download = `promo-${app.job.id.slice(0, 8)}.${format}`;
  document.body.append(link); link.click(); link.remove();
}));
$("#catalog-search").addEventListener("input", renderCatalog);
$("#catalog-region").addEventListener("change", renderCatalog);
initialize();
