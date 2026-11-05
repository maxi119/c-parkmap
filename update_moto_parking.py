#!/usr/bin/env python3
"""臺北市路邊機車收費路段 → Google My Maps 圖層 (KML)。

流程：
  1. 從停管處「停車收費資訊」頁找出《路邊機車收費路段一覽表》PDF
  2. PDF 未變更就結束（用 SHA-256 判斷）；有變更才往下做
  3. 解析 PDF 表格 → 用 OpenStreetMap 路網把「路段 + 起訖」轉成線段
  4. 輸出 KML（總表 + 依收費時段分層）、CSV、待人工確認清單、異動紀錄

用法：
  python update_moto_parking.py            # 檢查更新，有變更才重建
  python update_moto_parking.py --force    # 不管有無變更都重建
  python update_moto_parking.py --notify   # 有變更或失敗時跳 Windows 通知
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import logging
import math
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pdfplumber
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
CACHE = ROOT / "cache"
ARCHIVE = ROOT / "archive"
STATE_FILE = CACHE / "state.json"
OSM_FILE = CACHE / "osm_taipei_roads.json"
OVERRIDES_FILE = ROOT / "overrides.csv"
LOG_FILE = ROOT / "update.log"

INDEX_URL = "https://pma.gov.taipei/Content_List.aspx?n=0EC7D6A1032AA0D6"
PDF_LINK_KEYWORD = "路邊機車收費路段"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OVERPASS_QUERY = (
    '[out:json][timeout:180];area["name"="臺北市"]["admin_level"="4"]->.a;'
    'way(area.a)["highway"]["name"];out body geom;'
)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; gmap-moto-parking/1.0)"}
OVERPASS_HEADERS = {"User-Agent": "gmap-moto-parking/1.0"}   # Overpass 會拒絕仿瀏覽器的 User-Agent

OSM_MAX_AGE_DAYS = 30
MIN_EXPECTED_ROWS = 100     # 低於此列數視為 PDF 改版或解析失敗，不覆蓋既有輸出
CROSS_TOLERANCES_M = (30, 150)   # 道路與起訖路口的距離容許值：先嚴後寬，放寬者標為 near
DENSIFY_M = 12              # 路網內插間距：OSM 節點稀疏時仍能精準切在路口
GAP_BRIDGE_M = 120          # 同名道路在 OSM 斷開時，可視為相連的最大間隙
MAX_DETOUR_RATIO = 2.0      # 路徑長超過起訖直線距離的倍數就視為繞路（錯誤），不採用
MY_MAPS_LAYER_LIMIT = 10
MY_MAPS_FEATURE_LIMIT = 2000
MY_MAPS_FILE_LIMIT = 5 * 1024 * 1024

# 不是一般車道的道路類型（步道、樓梯、室內通道、施工中…）
EXCLUDED_HIGHWAY = {
    "footway", "steps", "path", "corridor", "cycleway", "bridleway", "elevator",
    "proposed", "construction", "raceway", "bus_guideway", "motorway", "motorway_link",
}
# 高架快速道路與平面道路常同名（如市民大道），機車格在平面層
ELEVATED_HIGHWAY = {"trunk", "trunk_link", "primary", "primary_link"}

CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# PDF 中出現的錯字或簡寫
NAME_FIXES = (("港漧", "港墘"), ("陽光3", "陽光街3"), ("基路路", "基隆路"), ("民敦煌路", "敦煌路"))

# KML 顏色格式 aabbggrr；依收費時段上色，對應不到的用灰色
SCHEDULE_COLORS = {
    "週一至六 9-17時": "ff2f6fe0",
    "週一至五 9-17時": "ffd08a1f",
    "週一至日 9-20時": "ff3030d0",
    "週一至日 9-21時": "ff8a2be2",
    "週一至六 9-20時": "ff30a030",
    "週一至六 9-18時": "ff00b4d8",
    "週一至日 12-20時": "ffa040a0",
    "週一至六 12-20時": "ff909020",
    "週六日及例假日 9-17時": "ff1f7fff",
}
DEFAULT_COLOR = "ff808080"

log = logging.getLogger("moto-parking")


# ---------------------------------------------------------------- 資料結構

@dataclass
class Row:
    region: str
    schedule: str
    road: str
    span: str

    @property
    def key(self) -> str:
        return f"{self.region}|{self.schedule}|{self.road}|{self.span}"


@dataclass
class Segment:
    row: Row
    quality: str                      # exact / near / approx / manual / missing
    note: str = ""
    lines: list[list[tuple[float, float]]] = field(default_factory=list)  # [(lon, lat), ...]


# ---------------------------------------------------------------- 下載與 PDF 解析

def find_pdf_link() -> tuple[str, str]:
    resp = requests.get(INDEX_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    for a in BeautifulSoup(resp.text, "html.parser").find_all("a", href=True):
        title = (a.get("title") or a.get_text()).strip()
        if PDF_LINK_KEYWORD in title:
            title = title.replace("[另開新視窗]", "").strip()
            return title, a["href"]
    raise RuntimeError(f"索引頁找不到含「{PDF_LINK_KEYWORD}」的連結，網站可能改版：{INDEX_URL}")


def download_pdf(url: str) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=120)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        raise RuntimeError(f"下載內容不是 PDF：{url}")
    return resp.content


def normalize_schedule(text: str) -> str:
    """'週一至六\\n9 -17 時' → '週一至六 9-17時'；'週六、日及例假日9-17' → '週六日及例假日 9-17時'"""
    s = re.sub(r"\s+", "", text).replace("、", "")
    m = re.match(r"^(.*?)(\d+-\d+)時?$", s)
    return f"{m.group(1)} {m.group(2)}時" if m else s


def parse_pdf(path: Path) -> tuple[list[Row], str]:
    rows: list[Row] = []
    region = schedule = ""
    made = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for raw in table:
                    cells = [(c or "").strip() for c in raw] + [""] * 4
                    first = cells[0].replace("\n", "")
                    if first.startswith("臺北市路邊機車"):
                        m = re.search(r"\d+年\d+月", first)
                        made = m.group(0) if m else made
                        continue
                    if first == "區域" or first.startswith("※"):
                        continue
                    if first and first != region:   # 換區域時，時段不沿用上一區
                        region, schedule = first, ""
                    if cells[1]:
                        schedule = normalize_schedule(cells[1])
                    road = cells[2].replace("\n", "")
                    span = cells[3].replace("\n", "")
                    if not road:
                        continue
                    if not schedule:
                        raise RuntimeError(f"路段缺少收費時段，PDF 版面可能改變：{road}")
                    rows.append(Row(region, schedule, road, span))
    if len(rows) < MIN_EXPECTED_ROWS:
        raise RuntimeError(f"只解析出 {len(rows)} 列（預期至少 {MIN_EXPECTED_ROWS}），PDF 版面可能改變")
    return rows, made


# ---------------------------------------------------------------- 路名正規化

def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).replace("臺", "台").replace(" ", "")
    s = re.sub(r"([一二三四五六七八九十])段", lambda m: f"{CN_DIGITS[m.group(1)]}段", s)
    for wrong, right in NAME_FIXES:
        s = s.replace(wrong, right)
    return s


def base_road(name: str) -> str:
    """'忠孝西路1段' → '忠孝西路'；含巷弄者不變，避免巷子被當成主幹道。"""
    return re.sub(r"\d+段$", "", name)


def split_road(raw: str) -> tuple[list[str], str]:
    """把 PDF 路段欄拆成可查詢的路名清單與附註。

    '中山南路(西側)'          → ['中山南路'], '西側'
    '南港路2、3段'            → ['南港路2段', '南港路3段'], ''
    '瑞光路358巷30、36、38弄' → ['瑞光路358巷30弄', ...], ''
    '市民大道3段、4段南側'    → ['市民大道3段', '市民大道4段'], '南側'
    """
    notes = re.findall(r"[(（]([^)）]*)[)）]", raw)
    s = norm_name(re.sub(r"[(（][^)）]*[)）]", "", raw))
    s = re.sub(r"含.*$", "", s)
    m = re.search(r"[東西南北]側$", s)
    if m and re.search(r"(路|街|段|巷|弄|道)[東西南北]側$", s):
        notes.append(m.group(0))
        s = s[: m.start()]
    s = re.sub(r"(?<=[路街段巷弄道])[A-Z]$", "", s)   # 中山南路A、博愛路D
    m = re.match(r"^(.*?)((?:\d+段?、)+\d+)(段|巷|弄)$", s)
    if m:
        numbers = re.findall(r"\d+", m.group(2))
        return [f"{m.group(1)}{n}{m.group(3)}" for n in numbers], "、".join(notes)
    return [s], "、".join(notes)


def split_span(span: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[-－—–~～至]", span) if p.strip()]
    if len(parts) >= 2:
        return [norm_name(parts[0]), norm_name(parts[-1])]
    return [norm_name(p) for p in parts]


# ---------------------------------------------------------------- OSM 路網

def to_xy(lat: float, lon: float) -> tuple[float, float]:
    return (lon - 121.5) * 100_800.0, (lat - 25.0) * 110_780.0


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def load_osm(force: bool = False) -> list[dict]:
    fresh = OSM_FILE.exists() and time.time() - OSM_FILE.stat().st_mtime < OSM_MAX_AGE_DAYS * 86400
    if fresh and not force:
        return json.loads(OSM_FILE.read_text(encoding="utf-8"))
    last_error: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            log.info("下載 OSM 路網：%s", url)
            resp = requests.post(url, data={"data": OVERPASS_QUERY}, headers=OVERPASS_HEADERS, timeout=300)
            resp.raise_for_status()
            ways = [w for w in resp.json()["elements"] if usable_way(w)]
            if len(ways) < 1000:
                raise RuntimeError(f"OSM 回傳道路過少：{len(ways)}")
            tmp = OSM_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(ways, ensure_ascii=False), encoding="utf-8")
            tmp.replace(OSM_FILE)
            return ways
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
            last_error = exc
            log.warning("Overpass 失敗：%s", exc)
    if OSM_FILE.exists():
        log.warning("改用過期的 OSM 快取")
        return json.loads(OSM_FILE.read_text(encoding="utf-8"))
    raise RuntimeError(f"無法取得 OSM 路網：{last_error}")


def usable_way(way: dict) -> bool:
    tags = way.get("tags", {})
    highway = tags.get("highway", "")
    if highway in EXCLUDED_HIGHWAY or "tunnel" in tags or "geometry" not in way:
        return False
    if tags.get("bridge") and highway in ELEVATED_HIGHWAY and int_or_zero(tags.get("layer")) >= 1:
        return False
    return True


def int_or_zero(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


class RoadIndex:
    def __init__(self, ways: list[dict]):
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        self.by_base: dict[str, list[dict]] = defaultdict(list)
        for way in ways:
            name = norm_name(way["tags"]["name"])
            self.by_name[name].append(way)
            self.by_base[base_road(name)].append(way)

    def lookup(self, name: str) -> list[dict]:
        """精確路名與「不分段」路名取聯集：'中華路' 要同時涵蓋中華路、中華路1段、中華路2段。"""
        found = {w["id"]: w for w in self.by_name.get(name, [])}
        found.update((w["id"], w) for w in self.by_base.get(name, []))
        return list(found.values())

    def lookup_relaxed(self, name: str) -> list[dict]:
        """PDF 段別可能有誤（松仁路實際接忠孝東路 5 段，表上寫 4 段）：放寬到同路各段。"""
        return self.lookup(name) or self.by_base.get(base_road(name), [])


class Graph:
    """由同名道路組成的無向圖；節點內插到約 DENSIFY_M 公尺一點。"""

    def __init__(self, ways: list[dict]):
        self.pts: dict = {}
        self.adj: dict = defaultdict(list)
        for way in ways:
            nodes = way["nodes"]
            geom = way["geometry"]
            for i, (nid, g) in enumerate(zip(nodes, geom)):
                self.pts[nid] = to_xy(g["lat"], g["lon"])
                if i == 0:
                    continue
                self._add_edge(way["id"], i, nodes[i - 1], nid)

    def _add_edge(self, way_id: int, seg: int, a, b) -> None:
        pa, pb = self.pts[a], self.pts[b]
        length = dist(pa, pb)
        steps = max(1, int(length // DENSIFY_M))
        prev = a
        for s in range(1, steps + 1):
            if s == steps:
                cur = b
            else:
                cur = (way_id, seg, s)
                t = s / steps
                self.pts[cur] = (pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t)
            d = length / steps
            self.adj[prev].append((cur, d))
            self.adj[cur].append((prev, d))
            prev = cur

    def components(self) -> list[set]:
        seen: set = set()
        comps = []
        for start in self.pts:
            if start in seen:
                continue
            stack, comp = [start], {start}
            while stack:
                for nxt, _ in self.adj[stack.pop()]:
                    if nxt not in comp:
                        comp.add(nxt)
                        stack.append(nxt)
            seen |= comp
            comps.append(comp)
        return comps

    def bridge_gaps(self, comps: list[set]) -> None:
        """OSM 中同名道路常在路口斷開；把相距很近的片段接起來。"""
        grid = defaultdict(list)
        cell = GAP_BRIDGE_M
        for idx, comp in enumerate(comps):
            for n in comp:
                x, y = self.pts[n]
                grid[(int(x // cell), int(y // cell))].append((idx, n))
        best: dict[tuple[int, int], tuple[float, object, object]] = {}
        for (cx, cy), items in grid.items():
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for ia, na in items:
                        for ib, nb in grid.get((cx + dx, cy + dy), ()):
                            if ia >= ib:
                                continue
                            d = dist(self.pts[na], self.pts[nb])
                            if d <= GAP_BRIDGE_M and d < best.get((ia, ib), (1e9,))[0]:
                                best[(ia, ib)] = (d, na, nb)
        for d, na, nb in best.values():
            self.adj[na].append((nb, d))
            self.adj[nb].append((na, d))

    def near_cross(self, cross_ways: list[dict], nodes: set, tolerance: float) -> list:
        """回傳與橫交道路相距 tolerance 內、且為區域最近的節點。"""
        cell = tolerance
        grid = defaultdict(list)
        for way in cross_ways:
            pts = [to_xy(g["lat"], g["lon"]) for g in way["geometry"]]
            for p, q in zip(pts, pts[1:]):
                steps = max(1, int(dist(p, q) // DENSIFY_M))
                for s in range(steps + 1):
                    t = s / steps
                    x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
                    grid[(int(x // cell), int(y // cell))].append((x, y))
        def distance_to_cross(n) -> float:
            x, y = self.pts[n]
            cx, cy = int(x // cell), int(y // cell)
            return min(
                (dist((x, y), q) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for q in grid.get((cx + dx, cy + dy), ())),
                default=math.inf,
            )

        # 取「局部最近點」而不是用距離去重：雙向分隔道路的兩條車道只相隔數十公尺，
        # 若併成一個候選點，起訖可能落在不同車道，路徑就會繞到遠處再折返。
        distances = {n: distance_to_cross(n) for n in nodes}
        return [
            n for n, d in distances.items()
            if d <= tolerance and all(d <= distances.get(m, math.inf) for m, _ in self.adj[n])
        ]

    def shortest_path(self, sources: list, targets: list, blocked: frozenset | set = frozenset()) -> tuple[float, list] | None:
        target_set = set(targets)
        queue = [(0.0, i, s) for i, s in enumerate(sources)]
        heapq.heapify(queue)
        best = {s: 0.0 for s in sources}
        prev: dict = {}
        tie = len(sources)
        while queue:
            d, _, node = heapq.heappop(queue)
            if d > best.get(node, math.inf):
                continue
            if node in target_set and d > 0:
                path = [node]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                return d, path[::-1]
            for nxt, weight in self.adj[node]:
                if nxt in blocked:
                    continue
                nd = d + weight
                if nd < best.get(nxt, math.inf):
                    best[nxt] = nd
                    prev[nxt] = node
                    tie += 1
                    heapq.heappush(queue, (nd, tie, nxt))
        return None

    def to_lonlat(self, path: list) -> list[tuple[float, float]]:
        line = []
        for n in path:
            x, y = self.pts[n]
            line.append((round(x / 100_800.0 + 121.5, 6), round(y / 110_780.0 + 25.0, 6)))
        return simplify(line)


def simplify(line: list[tuple[float, float]], tolerance_m: float = 1.5) -> list[tuple[float, float]]:
    """Douglas-Peucker：拿掉內插點，縮小 KML 檔案。"""
    if len(line) < 3:
        return line
    pts = [to_xy(lat, lon) for lon, lat in line]
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        seg = math.hypot(bx - ax, by - ay) or 1e-9
        far, far_d = None, tolerance_m
        for i in range(a + 1, b):
            d = abs((bx - ax) * (ay - pts[i][1]) - (ax - pts[i][0]) * (by - ay)) / seg
            if d > far_d:
                far, far_d = i, d
        if far is not None:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [p for p, k in zip(line, keep) if k]


def whole_ways(ways: list[dict]) -> list[list[tuple[float, float]]]:
    return [[(g["lon"], g["lat"]) for g in w["geometry"]] for w in ways]


def locate(row: Row, index: RoadIndex) -> Segment:
    names, note = split_road(row.road)
    ways: list[dict] = []
    for name in names:
        ways += index.lookup(name)
    unique = {w["id"]: w for w in ways}
    ways = list(unique.values())
    if not ways:
        return Segment(row, "missing", f"OSM 找不到路名：{'、'.join(names)}")

    ends = split_span(row.span)
    # 「XX巷底」就是這條巷子的盡頭，而且只有一個真正的路口：巷子本身就是答案
    if any(e.endswith("底") and e[:-1] in names for e in ends):
        return Segment(row, "exact", note, whole_ways(ways))
    cross = [[w for w in index.lookup_relaxed(e) if w["id"] not in unique] for e in ends]
    if len(ends) != 2 or not all(cross):
        bad = [e for e, c in zip(ends, cross) if not c] or [row.span or "(空白)"]
        return Segment(row, "approx", f"起訖點無法定位（{'、'.join(bad)}），畫出整條道路", whole_ways(ways))

    relaxed = [[w for w in index.by_base.get(base_road(e), []) if w["id"] not in unique] for e in ends]
    attempts = (
        (cross, CROSS_TOLERANCES_M[0], "exact"),
        (cross, CROSS_TOLERANCES_M[1], "near"),
        (relaxed, CROSS_TOLERANCES_M[1], "near"),
    )
    for cross_ways, tolerance, quality in attempts:
        lines = clip_between(ways, cross_ways, tolerance)
        if lines:
            return Segment(row, quality, note, keep_side(lines, note))
    return Segment(row, "approx", f"道路與起訖路口未相交（{row.span}），畫出整條道路", whole_ways(ways))


def keep_side(lines: list[list[tuple[float, float]]], note: str) -> list[list[tuple[float, float]]]:
    """PDF 註明「東側」等方位且該路有兩條平行車道時，只留該側。"""
    m = re.search(r"([東西南北])側", note)
    if not m or len(lines) < 2:
        return lines

    def center(line: list[tuple[float, float]]) -> tuple[float, float]:
        return sum(p[0] for p in line) / len(line), sum(p[1] for p in line) / len(line)

    side = m.group(1)
    axis = 0 if side in "東西" else 1
    choose = max if side in "東北" else min
    return [choose(lines, key=lambda line: center(line)[axis])]


def clean_paths(graph: Graph, starts: list, stops: list, max_paths: int = 2) -> list[list]:
    """找出最多兩條互不重疊的路徑（雙向道路的兩側），並排除繞路的結果。"""
    paths: list[list] = []
    first_length = 0.0
    blocked: set = set()
    for _ in range(max_paths):
        found = graph.shortest_path(starts, stops, blocked)
        if not found:
            break
        length, path = found
        straight = dist(graph.pts[path[0]], graph.pts[path[-1]])
        if length > MAX_DETOUR_RATIO * max(straight, DENSIFY_M):
            break
        if paths and length > 1.5 * first_length:   # 第二條比第一條長太多，不是對向車道
            break
        if not paths:
            first_length = length
        paths.append(path)
        blocked |= set(path)
        starts = [n for n in starts if n not in blocked]
        stops = [n for n in stops if n not in blocked]
        if not starts or not stops:
            break
    return paths


def clip_between(ways: list[dict], cross: list[list[dict]], tolerance: float) -> list[list[tuple[float, float]]]:
    if not all(cross):
        return []
    graph = Graph(ways)
    comps = graph.components()
    lines = []
    # 雙向分隔道路也可能是互不相連的兩個片段：每個片段各自切一段
    for comp in comps:
        starts = graph.near_cross(cross[0], comp, tolerance)
        stops = graph.near_cross(cross[1], comp, tolerance)
        if starts and stops:
            lines += [graph.to_lonlat(p) for p in clean_paths(graph, starts, stops)]
    if lines or len(comps) == 1:
        return lines
    # 起訖點落在不同片段：把斷開處接起來再找一次
    graph.bridge_gaps(comps)
    every_node = set(graph.pts)
    starts = graph.near_cross(cross[0], every_node, tolerance)
    stops = graph.near_cross(cross[1], every_node, tolerance)
    if starts and stops:
        return [graph.to_lonlat(p) for p in clean_paths(graph, starts, stops, max_paths=1)]
    return []


# ---------------------------------------------------------------- 人工修正

def load_overrides() -> dict[tuple[str, str], list[list[tuple[float, float]]]]:
    """overrides.csv 欄位：路段,路段起訖,座標（'緯度,經度;緯度,經度'，可從 Google 地圖複製）"""
    if not OVERRIDES_FILE.exists():
        return {}
    result = {}
    with OVERRIDES_FILE.open(encoding="utf-8-sig", newline="") as f:
        for rec in csv.DictReader(f):
            coords = []
            for pair in (rec.get("座標") or "").split(";"):
                if pair.strip():
                    lat, lon = (float(v) for v in pair.split(","))
                    coords.append((lon, lat))
            if len(coords) == 1:          # 單點也要能畫：補一個幾乎重疊的點
                coords.append((coords[0][0] + 0.00001, coords[0][1]))
            if coords:
                result[(rec["路段"].strip(), rec["路段起訖"].strip())] = [coords]
    return result


# ---------------------------------------------------------------- 輸出

def kml_document(title: str, segments: list[Segment], version: str) -> str:
    styles: dict[str, str] = {}
    marks: list[str] = []
    for seg in segments:
        row = seg.row
        approx = seg.quality == "approx"
        color = SCHEDULE_COLORS.get(row.schedule, DEFAULT_COLOR)
        style_id = f"s{color}{'a' if approx else ''}"
        styles[style_id] = (
            f'<Style id="{style_id}"><LineStyle><color>{color}</color>'
            f"<width>{2 if approx else 5}</width></LineStyle></Style>"
        )
        label = {"exact": "依路口切分", "near": "依路口切分（路口距離較遠，端點可能略有偏差）", "manual": "人工標註", "approx": "約略（整條道路，請以現場為準）"}[seg.quality]
        desc = (
            f"區域：{row.region}<br>收費時間：{row.schedule}<br>路段：{row.road}<br>"
            f"起訖：{row.span or '-'}<br>位置精度：{label}<br>"
            + (f"備註：{seg.note}<br>" if seg.note else "")
            + f"資料版本：{version}<br>來源：臺北市停車管理工程處；道路幾何 © OpenStreetMap 貢獻者"
        )
        name = f"{row.road}（{row.span}）" if row.span else row.road
        if approx:
            name = f"[約略] {name}"
        for line in seg.lines:
            coords = " ".join(f"{lon},{lat}" for lon, lat in line)
            marks.append(
                f"<Placemark><name>{escape(name)}</name>"
                f"<description><![CDATA[{desc}]]></description>"
                f"<styleUrl>#{style_id}</styleUrl>"
                f"<LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString>"
                "</Placemark>"
            )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{escape(title)}</name>" + "".join(styles.values()) + "\n" + "\n".join(marks) + "\n</Document></kml>\n"
    )


def safe_filename(schedule: str) -> str:
    return re.sub(r"[^\w\-]+", "_", schedule).strip("_")


def write_outputs(segments: list[Segment], version: str) -> None:
    tmp = OUTPUT.with_name("output.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "layers").mkdir(parents=True)

    drawn = [s for s in segments if s.lines]
    (tmp / "taipei_moto_parking_all.kml").write_text(
        kml_document(f"臺北市路邊機車收費路段（{version}）", drawn, version), encoding="utf-8"
    )
    groups: dict[str, list[Segment]] = defaultdict(list)
    for seg in drawn:
        groups[seg.row.schedule].append(seg)
    for schedule, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        path = tmp / "layers" / f"{safe_filename(schedule)}.kml"
        path.write_text(kml_document(f"機車收費 {schedule}", group, version), encoding="utf-8")
        features = sum(len(s.lines) for s in group)
        if features > MY_MAPS_FEATURE_LIMIT or path.stat().st_size > MY_MAPS_FILE_LIMIT:
            log.warning("%s 超過 My Maps 單層限制（%d 個線段）", path.name, features)
    if len(groups) > MY_MAPS_LAYER_LIMIT:
        log.warning("收費時段有 %d 種，超過 My Maps 每張地圖 %d 層的限制", len(groups), MY_MAPS_LAYER_LIMIT)

    with (tmp / "segments.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["區域", "收費時間", "路段", "路段起訖", "位置精度", "線段數", "備註"])
        for s in segments:
            w.writerow([s.row.region, s.row.schedule, s.row.road, s.row.span, s.quality, len(s.lines), s.note])
    with (tmp / "needs_review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["路段", "路段起訖", "區域", "位置精度", "原因"])
        for s in segments:
            if s.quality in ("approx", "missing"):
                w.writerow([s.row.road, s.row.span, s.row.region, s.quality, s.note])

    # output/CHANGES.md 是累積紀錄，要帶到新版輸出
    if (OUTPUT / "CHANGES.md").exists():
        shutil.copy2(OUTPUT / "CHANGES.md", tmp / "CHANGES.md")
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    tmp.replace(OUTPUT)


def record_changes(version: str, old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    path = OUTPUT / "CHANGES.md"
    previous = path.read_text(encoding="utf-8") if path.exists() else "# 異動紀錄\n"
    head, _, rest = previous.partition("\n")
    if old:
        lines = [f"\n## {datetime.now():%Y-%m-%d} {version}（新增 {len(added)}、移除 {len(removed)}）"]
        lines += [f"- 新增：{k.replace('|', ' / ')}" for k in added]
        lines += [f"- 移除：{k.replace('|', ' / ')}" for k in removed]
    else:
        lines = [f"\n## {datetime.now():%Y-%m-%d} {version}（首次建立，共 {len(new)} 段）"]
    path.write_text(head + "\n" + "\n".join(lines) + "\n" + rest, encoding="utf-8")
    return added, removed


def notify(title: str, message: str) -> None:
    """Windows 10/11 原生通知，不需要額外套件。"""
    if sys.platform != "win32":
        return
    safe_title, safe_msg = (s.replace("'", "''") for s in (title, message))
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
        "$n=$t.GetElementsByTagName('text');"
        f"$n.Item(0).AppendChild($t.CreateTextNode('{safe_title}'))|Out-Null;"
        f"$n.Item(1).AppendChild($t.CreateTextNode('{safe_msg}'))|Out-Null;"
        "$a='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($a).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=30, check=True,
                       capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("通知失敗：%s", exc)


# ---------------------------------------------------------------- 主流程

def run(force: bool) -> tuple[bool, str]:
    for d in (CACHE, ARCHIVE):
        d.mkdir(exist_ok=True)
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}

    title, url = find_pdf_link()
    pdf_bytes = download_pdf(url)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    changed = digest != state.get("sha256")
    if not changed and not force:
        log.info("PDF 未變更：%s", title)
        return False, "未變更"

    pdf_path = CACHE / "latest.pdf"
    pdf_path.write_bytes(pdf_bytes)
    rows, made = parse_pdf(pdf_path)
    version = made or datetime.now().strftime("%Y-%m-%d")
    log.info("解析 %d 列，版本 %s", len(rows), version)
    if changed:
        shutil.copy2(pdf_path, ARCHIVE / f"{datetime.now():%Y%m%d}_{digest[:8]}.pdf")

    index = RoadIndex(load_osm(force=False))
    overrides = load_overrides()
    segments = []
    for row in rows:
        manual = overrides.get((row.road, row.span))
        segments.append(Segment(row, "manual", "", manual) if manual else locate(row, index))
    write_outputs(segments, version)

    counts = Counter(s.quality for s in segments)
    log.info("定位結果：%s", dict(counts))
    summary = f"{version}：共 {len(rows)} 段，依路口切分 {counts['exact'] + counts['near'] + counts['manual']}、約略 {counts['approx']}、未畫出 {counts['missing']}"
    if changed:
        added, removed = record_changes(version, state.get("rows", []), [r.key for r in rows])
        summary += f"；新增 {len(added)}、移除 {len(removed)}"

    # 全部成功才更新狀態，失敗時下次會重試
    STATE_FILE.write_text(json.dumps(
        {"sha256": digest, "title": title, "url": url, "version": version,
         "checked": datetime.now().isoformat(timespec="seconds"), "rows": [r.key for r in rows]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(summary)
    return changed, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="PDF 未變更也重建")
    parser.add_argument("--notify", action="store_true", help="有變更或失敗時跳 Windows 通知")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    try:
        changed, summary = run(args.force)
    except Exception as exc:  # 排程環境沒人看 console：所有失敗都要留紀錄並通知
        log.exception("更新失敗，既有輸出未更動")
        if args.notify:
            notify("機車收費路段更新失敗", str(exc)[:150])
        return 1
    if changed and args.notify:
        notify("機車收費路段有更新，請重新匯入 My Maps", summary[:150])
    return 0


if __name__ == "__main__":
    sys.exit(main())
