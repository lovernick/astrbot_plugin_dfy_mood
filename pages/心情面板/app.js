const DIMS = [
  { key: "valence", name: "愉悦", color: "#ff7a59" },
  { key: "arousal", name: "活跃", color: "#f4a259" },
  { key: "energy", name: "精力", color: "#7fd1de" },
  { key: "intimacy", name: "亲密", color: "#f17fa7" },
  { key: "jealousy", name: "吃醋", color: "#c9527f" },
];

const RANGE_SECONDS = {
  "24h": 24 * 3600,
  "7d": 7 * 24 * 3600,
  "30d": 30 * 24 * 3600,
};

const DELTA_LABEL = {
  valence: "愉悦",
  arousal: "活跃",
  energy: "精力",
  intimacy: "亲密",
  jealousy: "吃醋",
};

const state = {
  session: "",
  range: "24h",
  tab: "overview",
  sessions: [],
  configFields: [],
  charts: {},
  service: null,
};

function service() {
  return window.AstrBotPluginPage || window.AstrBotPluginView || null;
}

function showToast(text) {
  const box = document.getElementById("toast");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    box.hidden = true;
  }, 2600);
}

function fmtTime(ts, withDate = false) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  const time = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return withDate ? `${p(d.getMonth() + 1)}-${p(d.getDate())} ${time}` : time;
}

function ago(ts) {
  if (!ts) return "还没聊过";
  const diff = Math.max(0, Date.now() / 1000 - ts);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function isDark() {
  return document.documentElement.getAttribute("data-theme") === "dark";
}

function axisStyle() {
  const color = isDark() ? "#b6a396" : "#8b7a6e";
  const line = isDark() ? "rgba(255,255,255,0.12)" : "rgba(58,42,34,0.12)";
  return { color, line };
}

function labelDescription(label, v) {
  const map = {
    吃醋: "有点酸，正在等你哄",
    烦躁: "耐心不多，说话会变短",
    委屈: "等你来顺毛",
    低落: "提不起精神",
    困了: "精力见底，想睡觉",
    无聊: "想找点事做",
    撒娇: "黏人模式已开启",
    开心: "心情很好，愿意多说几句",
    平静: "没什么波动，正常待机",
  };
  let base = map[label] || "没什么波动";
  if (label === "吃醋") base += `（吃醋 ${Math.round(v)}/100）`;
  return base;
}

async function apiGet(endpoint, params) {
  const svc = service();
  return svc.apiGet(endpoint, params || {});
}

async function apiPost(endpoint, body) {
  const svc = service();
  return svc.apiPost(endpoint, body || {});
}

function setChartsTheme() {
  const { color, line } = axisStyle();
  Object.values(state.charts).forEach((chart) => {
    if (!chart) return;
    chart.setOption({
      textStyle: { color },
      xAxis: { axisLine: { lineStyle: { color: line } }, axisLabel: { color } },
      yAxis: { axisLine: { lineStyle: { color: line } }, axisLabel: { color } },
    });
  });
}

function ensureChart(id) {
  if (state.charts[id]) return state.charts[id];
  if (typeof echarts === "undefined") return null;
  const el = document.getElementById(id);
  const chart = echarts.init(el);
  state.charts[id] = chart;
  return chart;
}

function renderRings(values) {
  const box = document.getElementById("rings");
  box.innerHTML = "";
  DIMS.forEach((dim) => {
    const value = Math.round(values[dim.key] || 0);
    const item = document.createElement("div");
    item.className = "ring";
    item.innerHTML = `
      <div class="ring-dial" style="--p:${value};--color:${dim.color}">
        <span class="ring-value">${value}</span>
      </div>
      <span class="ring-name">${dim.name}</span>`;
    box.appendChild(item);
  });
}

function deltaChips(delta) {
  return Object.entries(delta || {})
    .filter(([, value]) => Number(value) !== 0)
    .map(([key, value]) => {
      const name = DELTA_LABEL[key] || key;
      const cls = Number(value) < 0 ? "chip down" : "chip";
      return `<span class="${cls}">${name}${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(0)}</span>`;
    })
    .join("");
}

function renderOverview(data) {
  const current = data.state;
  if (!current) {
    document.getElementById("mood-label").textContent = "还没有数据";
    document.getElementById("mood-desc").textContent = "先私聊它一句，再来看看心情吧";
    document.getElementById("mood-badge").textContent = "强度 · 轻";
    document.getElementById("rings").innerHTML = "";
    document.getElementById("subtitle").textContent = "还没有任何会话";
    return;
  }
  document.getElementById("mood-label").textContent = current.label;
  document.getElementById("mood-desc").textContent = labelDescription(current.label, current.jealousy);
  document.getElementById("mood-badge").textContent = `强度 · ${current.mood_intensity || intensityOf(current)}`;
  const platform = current.session_key.split(":")[0];
  document.getElementById("subtitle").textContent =
    `${platform} · ${ago(current.last_interact_at)}互动 · 最近更新 ${fmtTime(current.updated_at)}`;
  renderRings(current);
}

function intensityOf(current) {
  const base = { valence: 60, arousal: 55, energy: 70, jealousy: 0 };
  const score = Math.max(
    Math.abs(current.valence - base.valence) / 60,
    Math.abs(current.arousal - base.arousal) / 50,
    current.jealousy / 55
  );
  if (score >= 0.66) return "强";
  if (score >= 0.33) return "中";
  return "轻";
}

function renderFeed(list, target) {
  const box = document.getElementById(target);
  if (!list.length) {
    box.innerHTML = `<li class="empty">还没有记录到情绪变化，多聊几句试试</li>`;
    return;
  }
  box.innerHTML = list
    .map(
      (item) => `
      <li>
        <span class="feed-time">${fmtTime(item.ts, true)}</span>
        <div class="feed-body">
          <div class="feed-title">${item.label}</div>
          <div class="feed-reason">${item.reason || "—"}</div>
          <div class="chips">${deltaChips(item.delta)}</div>
        </div>
      </li>`
    )
    .join("");
}

function lineOption(points, useArea, rangeSeconds) {
  const { color, line } = axisStyle();
  const last = points.length ? points[points.length - 1].ts * 1000 : 0;
  const end = Math.max(Date.now(), last) + 1500;
  const start = end - (rangeSeconds || RANGE_SECONDS["24h"]) * 1000;
  return {
    animationDuration: 600,
    grid: { left: 42, right: 18, top: 42, bottom: 34 },
    tooltip: {
      trigger: "axis",
      backgroundColor: isDark() ? "rgba(42,33,28,0.95)" : "rgba(255,255,255,0.95)",
      borderColor: line,
      textStyle: { color: isDark() ? "#f6ece1" : "#3a2a22", fontSize: 12 },
      valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(0)}`),
    },
    legend: {
      top: 8,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color, fontSize: 11 },
    },
    xAxis: {
      type: "time",
      min: start,
      max: end,
      axisLabel: {
        color,
        fontSize: 11,
        hideOverlap: true,
        formatter: (value) => {
          const d = new Date(value);
          const p = (n) => String(n).padStart(2, "0");
          const time = `${p(d.getHours())}:${p(d.getMinutes())}`;
          return (rangeSeconds || RANGE_SECONDS["24h"]) <= RANGE_SECONDS["24h"]
            ? time
            : `${p(d.getMonth() + 1)}-${p(d.getDate())} ${time}`;
        },
      },
      axisLine: { lineStyle: { color: line } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 100,
      axisLabel: { color, fontSize: 11 },
      splitLine: { lineStyle: { color: line, type: "dashed" } },
    },
    series: DIMS.map((dim) => ({
      name: dim.name,
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 4,
      showSymbol: points.length < 40,
      lineStyle: { width: 2.2, color: dim.color },
      itemStyle: { color: dim.color },
      areaStyle: useArea ? { opacity: 0.08, color: dim.color } : undefined,
      data: points.map((p) => [p.ts * 1000, p[dim.key]]),
    })),
  };
}

function renderCharts(points, stats, rangeSeconds) {
  const overviewChart = ensureChart("chart-overview");
  if (overviewChart) overviewChart.setOption(lineOption(points, true, rangeSeconds));

  const historyChart = ensureChart("chart-history");
  if (historyChart) historyChart.setOption(lineOption(points, false, rangeSeconds));

  const kindsChart = ensureChart("chart-kinds");
  if (kindsChart) {
    const { color, line } = axisStyle();
    const items = (stats.kinds || []).slice().sort((a, b) => b.count - a.count);
    kindsChart.setOption(
      {
        animationDuration: 500,
        grid: { left: 96, right: 24, top: 12, bottom: 24 },
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: {
          type: "value",
          axisLabel: { color, fontSize: 11 },
          splitLine: { lineStyle: { color: line, type: "dashed" } },
        },
        yAxis: {
          type: "category",
          inverse: true,
          data: items.map((item) => item.label || item.kind),
          axisLabel: { color, fontSize: 11 },
          axisLine: { lineStyle: { color: line } },
        },
        series: [
          {
            type: "bar",
            barWidth: 12,
            itemStyle: {
              borderRadius: [0, 8, 8, 0],
              color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: "#ff9d7f" },
                { offset: 1, color: "#e85d3c" },
              ]),
            },
            data: items.map((item) => item.count),
          },
        ],
      }
    );
  }

  const hoursChart = ensureChart("chart-hours");
  if (hoursChart) {
    const { color, line } = axisStyle();
    const counts = new Array(24).fill(0);
    (stats.hours || []).forEach((item) => {
      if (item.hour >= 0 && item.hour < 24) counts[item.hour] = item.count;
    });
    hoursChart.setOption(
      {
        animationDuration: 500,
        grid: { left: 36, right: 18, top: 18, bottom: 26 },
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: {
          type: "category",
          data: counts.map((_, index) => `${index}`),
          axisLabel: { color, fontSize: 10, interval: 3 },
          axisLine: { lineStyle: { color: line } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color, fontSize: 11 },
          splitLine: { lineStyle: { color: line, type: "dashed" } },
        },
        series: [
          {
            type: "bar",
            barWidth: "55%",
            itemStyle: { borderRadius: [6, 6, 0, 0], color: "#7fd1de" },
            data: counts,
          },
        ],
      }
    );
  }
}

async function loadSessions() {
  const data = await apiGet("sessions");
  state.sessions = data.sessions || [];
  const select = document.getElementById("session-select");
  select.innerHTML = "";
  if (!state.sessions.length) {
    const option = document.createElement("option");
    option.textContent = "还没有会话";
    option.value = "";
    select.appendChild(option);
    state.session = "";
    return;
  }
  state.sessions.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.session_key;
    option.textContent = `${item.label} · ${item.platform} · ${item.uid}`;
    select.appendChild(option);
  });
  if (!state.session || !state.sessions.some((item) => item.session_key === state.session)) {
    state.session = state.sessions[0].session_key;
  }
  select.value = state.session;
}

async function loadOverview() {
  const data = await apiGet("overview", { session: state.session, range: state.range });
  renderOverview(data);
  renderFeed(data.events || [], "feed");
  const overviewChart = ensureChart("chart-overview");
  if (overviewChart) {
    overviewChart.setOption(
      lineOption(data.points || [], true, RANGE_SECONDS[state.range])
    );
  }
}

async function loadHistory() {
  const data = await apiGet("history", { session: state.session, range: state.range });
  const stats = await apiGet("stats", { session: state.session, range: state.range });
  const points = data.points || [];
  const windowSec = RANGE_SECONDS[state.range];
  const span = points.length > 1 ? points[points.length - 1].ts - points[0].ts : 0;
  document.getElementById("history-hint").textContent =
    span && span < windowSec / 10
      ? `目前只有最近 ${Math.max(1, Math.round(span / 60))} 分钟的数据`
      : "点的疏密取决于聊天频率";
  renderCharts(points, stats, windowSec);
  renderFeed(data.events || [], "event-feed");
  document.getElementById("event-hint").textContent = `${(data.events || []).length} 条`;
}

async function loadSessionsTab() {
  await loadSessions();
  const body = document.getElementById("session-body");
  if (!state.sessions.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">还没有会话，先私聊它一句</td></tr>`;
    return;
  }
  body.innerHTML = state.sessions
    .map(
      (item) => `
      <tr data-session="${item.session_key}">
        <td><span class="tag">${item.label}</span><br><span class="session-key">${item.session_key}</span></td>
        <td>${item.last_reason || "—"}</td>
        <td>${Math.round(item.intimacy)}</td>
        <td>${ago(item.last_interact_at)}</td>
        <td>${item.recent_messages}</td>
      </tr>`
    )
    .join("");
  body.querySelectorAll("tr[data-session]").forEach((row) => {
    row.addEventListener("click", async () => {
      state.session = row.dataset.session;
      document.getElementById("session-select").value = state.session;
      switchTab("overview");
      await loadOverview();
    });
  });
}

function renderSettings(fields) {
  state.configFields = fields;
  const form = document.getElementById("settings-form");
  form.innerHTML = "";
  fields.forEach((field) => {
    if (field.type === "bool") {
      const row = document.createElement("label");
      row.className = "switch-row";
      row.innerHTML = `
        <span>${field.label}</span>
        <input type="checkbox" data-key="${field.key}" ${field.value ? "checked" : ""} />`;
      form.appendChild(row);
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "field";
    const type = field.type === "str" ? "text" : "number";
    const step = field.type === "float" ? "0.1" : "1";
    wrap.innerHTML = `
      <label>${field.label}</label>
      <input type="${type}" data-key="${field.key}" value="${field.value ?? ""}" step="${step}" />`;
    form.appendChild(wrap);
  });
}

async function loadSettings() {
  const data = await apiGet("config");
  renderSettings(data.fields || []);
}

async function saveSettings(event) {
  event.preventDefault();
  const changes = {};
  state.configFields.forEach((field) => {
    const input = document.querySelector(`[data-key="${field.key}"]`);
    if (!input) return;
    const next = field.type === "bool" ? input.checked : input.value;
    if (String(next) !== String(field.value) && !(field.type === "bool" && next === field.value)) {
      changes[field.key] = next;
    }
  });
  if (!Object.keys(changes).length) {
    showToast("没有改动，无需保存");
    return;
  }
  const status = document.getElementById("settings-status");
  status.textContent = "保存中…";
  try {
    const data = await apiPost("config", { changes });
    renderSettings(data.fields || []);
    status.textContent = data.message || "已保存";
    showToast(data.applied ? "设置已保存并生效" : "设置已保存，重载后生效");
  } catch (error) {
    status.textContent = "保存失败";
    showToast(String(error.message || error));
  }
}

async function resetSession() {
  if (!state.session) {
    showToast("没有可重置的会话");
    return;
  }
  if (!window.confirm("确定把当前会话的心情重置为基线吗？历史事件会保留。")) return;
  await apiPost("reset", { session: state.session });
  showToast("心情已重置");
  await loadOverview();
  if (state.tab === "history") await loadHistory();
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll("#tabs button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tab}`);
  });
  if (tab === "overview") loadOverview();
  if (tab === "history") loadHistory();
  if (tab === "sessions") loadSessionsTab();
  if (tab === "settings") loadSettings();
  requestAnimationFrame(() => {
    Object.values(state.charts).forEach((chart) => chart.resize());
  });
}

async function refreshAll() {
  await loadSessions();
  await loadOverview();
  if (state.tab === "history") await loadHistory();
  if (state.tab === "sessions") await loadSessionsTab();
}

let autoTimer = null;
let autoBusy = false;

async function autoRefresh() {
  if (document.hidden || autoBusy || state.tab === "settings") return;
  autoBusy = true;
  try {
    await loadOverview();
    if (state.tab === "history") await loadHistory();
    if (state.tab === "sessions") await loadSessionsTab();
  } catch (error) {
    // 自动刷新失败时静默，等下一轮重试
  } finally {
    autoBusy = false;
  }
}

function startAutoRefresh() {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = setInterval(autoRefresh, 8000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) autoRefresh();
  });
}

function bindEvents() {
  document.getElementById("tabs").addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-tab]");
    if (btn) switchTab(btn.dataset.tab);
  });

  document.getElementById("session-select").addEventListener("change", async (event) => {
    state.session = event.target.value;
    await loadOverview();
    if (state.tab === "history") await loadHistory();
  });

  document.getElementById("range-switch").addEventListener("click", async (event) => {
    const btn = event.target.closest("button[data-range]");
    if (!btn) return;
    state.range = btn.dataset.range;
    document.querySelectorAll("#range-switch button").forEach((item) => {
      item.classList.toggle("active", item === btn);
    });
    if (state.tab === "history") await loadHistory();
    if (state.tab === "overview") await loadOverview();
  });

  document.getElementById("refresh-btn").addEventListener("click", refreshAll);
  document.getElementById("settings-form").addEventListener("submit", saveSettings);
  document.getElementById("reset-btn").addEventListener("click", resetSession);

  window.addEventListener("resize", () => {
    Object.values(state.charts).forEach((chart) => chart.resize());
  });
}

async function main() {
  const svc = service();
  if (!svc) {
    document.getElementById("subtitle").textContent = "请从 AstrBot 插件详情页打开这个面板";
    return;
  }
  if (svc.ready) {
    await Promise.race([svc.ready(), new Promise((resolve) => setTimeout(resolve, 4000))]);
  }
  if (svc.onContext) {
    svc.onContext(() => setChartsTheme());
  }
  bindEvents();
  try {
    await loadSessions();
    await loadOverview();
  } catch (error) {
    document.getElementById("subtitle").textContent = `读取数据失败：${error.message || error}`;
  }
  startAutoRefresh();
}

main();
