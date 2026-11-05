"use strict";

// 底圖集中在這裡，要換服務時只改這一處
const BASEMAP = {
  url: "https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}",
  attribution: '底圖 © <a href="https://maps.nlsc.gov.tw/" target="_blank" rel="noopener">內政部國土測繪中心</a>',
  maxNativeZoom: 19,
};
const ROAD_ATTRIBUTION = '道路 © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>';
const TAIPEI_CENTER = [25.05, 121.54];
const STALE_DAYS = 21;
const MAX_RESULTS = 30;
const QUALITY_LABEL = {
  exact: "依路口切分",
  near: "依路口切分（路口距離較遠，端點可能略有偏差）",
  manual: "人工標註",
  approx: "約略位置（整條道路，請以現場為準）",
};
const WEEKDAY_INDEX = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };

const el = (id) => document.getElementById(id);
const hiddenSchedules = new Set();
let items = [];          // { props, layers: [polyline], search }
let missing = [];
let status = null;
let nowOnly = false;
let query = "";
let locationMarker = null;

// 固定視野：少數路段遠在市區外，用資料範圍定位會把主要路段縮得太小
const map = L.map("map", { zoomControl: false, attributionControl: false, maxZoom: 20, minZoom: 10 })
  .setView(TAIPEI_CENTER, 13);
L.control.zoom({ position: "topleft" }).addTo(map);
L.control.attribution({ prefix: '<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>' })
  .addAttribution(ROAD_ATTRIBUTION)
  .addTo(map);
L.tileLayer(BASEMAP.url, {
  attribution: BASEMAP.attribution,
  maxNativeZoom: BASEMAP.maxNativeZoom,
  maxZoom: 20,
}).addTo(map);
// tolerance 加大點擊範圍：手機上用手指很難點中細線
const renderer = L.canvas({ tolerance: 10 });
const drawn = L.layerGroup().addTo(map);

/** 全市視角下線條不能太粗，否則相鄰路段會互相蓋住。 */
function lineWeight(approx) {
  const zoom = map.getZoom();
  const base = zoom <= 12 ? 2 : zoom === 13 ? 2.5 : zoom === 14 ? 3.5 : 5;
  return approx ? Math.max(1.5, base - 1.5) : base;
}

function makeEl(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;   // PDF 字串一律走 textContent，不進 innerHTML
  if (className) node.className = className;
  return node;
}

function normalize(text) {
  return text.normalize("NFKC").replace(/臺/g, "台").replace(/\s+/g, "").toLowerCase();
}

function formatTime(iso) {
  return new Date(iso).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei", year: "numeric", month: "numeric", day: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** 一律以臺北時間判斷，不用裝置時區。 */
function taipeiNow() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei", weekday: "short", hour: "numeric", minute: "numeric", hourCycle: "h23",
  }).formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type).value;
  return { day: WEEKDAY_INDEX[get("weekday")], hours: Number(get("hour")) + Number(get("minute")) / 60 };
}

/** 時段無法解析時回傳 true：寧可多顯示，也不要把可能收費的路段藏起來。 */
function isChargingNow(props, now) {
  if (!props.days.length) return true;
  return props.days.includes(now.day) && now.hours >= props.start && now.hours < props.end;
}

function popupContent(props) {
  const root = makeEl("div", undefined, "popup");
  root.append(makeEl("h3", props.road));
  if (props.quality === "approx") root.append(makeEl("p", "約略位置，實際範圍請以現場為準", "approx"));
  const list = makeEl("dl");
  const rows = [
    ["起訖", props.span || "-"],
    ["區域", props.region],
    ["收費", props.schedule],
    ["精度", QUALITY_LABEL[props.quality] || props.quality],
  ];
  if (props.note) rows.push(["備註", props.note]);
  for (const [term, value] of rows) list.append(makeEl("dt", term), makeEl("dd", value));
  root.append(list);
  return root;
}

function buildItems(geojson, colors) {
  items = geojson.features.map((feature) => {
    const props = feature.properties;
    const approx = props.quality === "approx";
    const color = colors[props.schedule] || "#808080";
    const layers = feature.geometry.coordinates.map((line) =>
      L.polyline(line.map(([lon, lat]) => [lat, lon]), {
        renderer,
        color,
        weight: lineWeight(approx),
        opacity: approx ? 0.55 : 0.9,
        dashArray: approx ? "6 7" : null,
      }).bindPopup(() => popupContent(props), { maxWidth: 280 }));
    return { props, layers, search: normalize(`${props.road} ${props.span} ${props.region}`) };
  });
}

function restyle() {
  for (const item of items) {
    const weight = lineWeight(item.props.quality === "approx");
    item.layers.forEach((layer) => layer.setStyle({ weight }));
  }
}

function matchesQuery(text) {
  return !query || text.includes(query);
}

function isVisible(item, now) {
  return !hiddenSchedules.has(item.props.schedule)
    && matchesQuery(item.search)
    && (!nowOnly || isChargingNow(item.props, now));
}

function render() {
  const now = taipeiNow();
  drawn.clearLayers();
  for (const item of items) {
    if (isVisible(item, now)) item.layers.forEach((layer) => drawn.addLayer(layer));
  }
  const note = el("now-note");
  note.hidden = !nowOnly;
  if (nowOnly) {
    note.textContent = "「現在收費中」只依星期與時間（臺北時間）判斷，國定假日未納入判斷；假日請以現場告示為準。";
  }
  renderResults();
}

function focusItem(item) {
  const bounds = L.featureGroup(item.layers).getBounds();
  map.fitBounds(bounds, { maxZoom: 17, padding: [30, 30] });
  item.layers[0].openPopup();
  closePanelOnMobile();
}

function renderResults() {
  const list = el("search-results");
  const count = el("search-count");
  list.replaceChildren();
  count.hidden = !query;
  if (!query) return;

  const now = taipeiNow();
  const found = items.filter((item) => isVisible(item, now));
  const absent = missing.filter((m) => matchesQuery(normalize(`${m.road} ${m.span} ${m.region}`)));
  count.textContent = `符合 ${found.length} 段` + (absent.length ? `，另有 ${absent.length} 段未畫出` : "");

  for (const item of found.slice(0, MAX_RESULTS)) {
    const button = makeEl("button", item.props.road, "result");
    button.type = "button";
    button.append(makeEl("small", `${item.props.span || "-"}・${item.props.schedule}`));
    button.addEventListener("click", () => focusItem(item));
    const row = makeEl("li");
    row.append(button);
    list.append(row);
  }
  for (const m of absent.slice(0, MAX_RESULTS)) {
    const button = makeEl("button", m.road, "result");
    button.type = "button";
    button.disabled = true;
    button.append(makeEl("small", `地圖上未畫出・${m.span || "-"}・${m.schedule}`));
    const row = makeEl("li");
    row.append(button);
    list.append(row);
  }
  if (found.length + absent.length > MAX_RESULTS) {
    list.append(makeEl("li", `僅列前 ${MAX_RESULTS} 筆，請輸入更完整的路名。`, "hint"));
  }
}

function renderLegend() {
  const legend = el("legend");
  legend.replaceChildren();
  for (const entry of status.schedules) {
    const label = makeEl("label");
    const box = makeEl("input");
    box.type = "checkbox";
    box.checked = !hiddenSchedules.has(entry.schedule);
    box.addEventListener("change", () => {
      if (box.checked) hiddenSchedules.delete(entry.schedule);
      else hiddenSchedules.add(entry.schedule);
      updateLegendAll();
      render();
    });
    const swatch = makeEl("span", undefined, "swatch");
    swatch.style.background = entry.color;
    label.append(box, swatch, makeEl("span", entry.schedule), makeEl("span", String(entry.count), "count"));
    const row = makeEl("li");
    row.append(label);
    legend.append(row);
  }
  updateLegendAll();
}

function updateLegendAll() {
  el("legend-all").textContent = hiddenSchedules.size === status.schedules.length ? "全部開啟" : "全部關閉";
}

function renderMissing() {
  el("missing-summary").textContent = `未畫出的路段（${missing.length}）`;
  const list = el("missing");
  list.replaceChildren();
  for (const m of missing) {
    const row = makeEl("li", `${m.road}（${m.span || "-"}）`);
    row.append(makeEl("br"), makeEl("small", `${m.region}・${m.schedule}`));
    list.append(row);
  }
}

function renderDownloads() {
  const list = el("downloads");
  for (const entry of status.schedules) {
    const link = makeEl("a", `${entry.schedule} KML`);
    link.href = encodeURI(entry.layer);
    link.download = "";
    const row = makeEl("li");
    row.append(link);
    list.append(row);
  }
}

function renderStatus() {
  el("status").textContent = `${status.version}版・檢查於 ${formatTime(status.checked_at)}`;
  el("source-version").textContent = `（${status.version}）`;
  el("source-link").href = status.source_page;
  const ageDays = (Date.now() - new Date(status.checked_at).getTime()) / 86400000;
  const stale = el("stale");
  stale.hidden = ageDays <= STALE_DAYS;
  if (!stale.hidden) {
    stale.textContent = `資料可能過期：已超過 ${STALE_DAYS} 天未檢查官方資料，請以現場標線與官方公告為準。`;
  }
}

function showToast(message, ms = 4000) {
  const toast = el("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, ms);
}

function setPanel(open) {
  el("panel").classList.toggle("open", open);
  el("panel-toggle").setAttribute("aria-expanded", String(open));
  el("panel-toggle").textContent = open ? "收合" : "圖例與資訊";
  map.invalidateSize();
}

function closePanelOnMobile() {
  if (window.matchMedia("(max-width: 799px)").matches) setPanel(false);
}

function bindControls() {
  el("panel-toggle").addEventListener("click", () => setPanel(!el("panel").classList.contains("open")));

  el("now-toggle").addEventListener("click", () => {
    nowOnly = !nowOnly;
    el("now-toggle").setAttribute("aria-pressed", String(nowOnly));
    render();
    if (nowOnly) showToast("以臺北時間判斷，國定假日未納入判斷", 6000);
  });
  setInterval(() => { if (nowOnly) render(); }, 60000);

  el("legend-all").addEventListener("click", () => {
    if (hiddenSchedules.size === status.schedules.length) hiddenSchedules.clear();
    else status.schedules.forEach((s) => hiddenSchedules.add(s.schedule));
    renderLegend();
    render();
  });

  let timer;
  el("search").addEventListener("input", (event) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      query = normalize(event.target.value);
      render();
    }, 150);
  });

  el("locate").addEventListener("click", () => {
    showToast("定位中…");
    map.locate({ setView: true, maxZoom: 16, timeout: 10000 });
  });
  map.on("locationfound", (event) => {
    if (locationMarker) locationMarker.remove();
    locationMarker = L.circleMarker(event.latlng, {
      radius: 8, color: "#fff", weight: 3, fillColor: "#1a73e8", fillOpacity: 1,
    }).addTo(map);
    el("toast").hidden = true;
  });
  map.on("zoomend", restyle);
  map.on("locationerror", () => showToast("無法取得位置，請確認瀏覽器已允許定位"));
}

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function init() {
  bindControls();
  // status.json 每次檢查都會變，要求瀏覽器重新驗證，避免顯示舊的檢查時間
  const [statusData, geojson, missingData] = await Promise.all([
    fetchJson("data/status.json", { cache: "no-cache" }),
    fetchJson("data/segments.geojson", { cache: "no-cache" }),
    fetchJson("data/missing.json", { cache: "no-cache" }),
  ]);
  status = statusData;
  missing = missingData;
  buildItems(geojson, Object.fromEntries(status.schedules.map((s) => [s.schedule, s.color])));
  renderStatus();
  renderLegend();
  renderMissing();
  renderDownloads();
  render();
}

init().catch((err) => {
  console.error(err);
  el("status").textContent = "資料載入失敗";
  const error = el("error");
  error.textContent = "資料載入失敗，請重新整理頁面。";
  error.hidden = false;
});
