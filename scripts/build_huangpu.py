#!/usr/bin/env python3
"""黄浦区全量建筑建模：OSM (Overpass JSON) -> glTF/GLB 模型 + 清单 (CSV/JSON/GeoJSON)。

用法:
    python3 scripts/build_huangpu.py [--input data/huangpu_osm.json] [--output output]

设计说明见 output/README.md。依赖: numpy shapely mapbox_earcut matplotlib (预览图可选)。
"""
import argparse
import csv
import json
import math
import os
import re
import shutil
import struct
import sys
from collections import Counter, defaultdict

import numpy as np
import mapbox_earcut as earcut
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

TILE_SIZE = 500.0          # 分块边长 (m)
NEIGHBOR_RADIUS = 150.0    # 邻域推算半径 (m)
ALL_GLB_LIMIT = 95e6       # 全区合并 glb 的上限 (字节)；GitHub 单文件上限 100 MB
EPS_AREA = 1e-6

# --------------------------------------------------------------------------------------
# 标签解析
# --------------------------------------------------------------------------------------
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_float(v):
    """取字符串里的第一个浮点数；带 ft / ' 的换算成米。"""
    if v is None:
        return None
    m = NUM_RE.search(str(v))
    if not m:
        return None
    x = float(m.group(0))
    s = str(v).lower()
    if "ft" in s or "'" in s or "feet" in s:
        x *= 0.3048
    return x


def parse_height(v):
    x = parse_float(v)
    if x is None or x <= 0 or x > 1000:
        return None
    return x


def parse_levels(v):
    """'14.' -> 14, '8;6' -> 8, '0' -> None。"""
    x = parse_float(v)
    if x is None:
        return None
    n = int(math.floor(x + 0.5))
    return n if n > 0 else None


def parse_nonneg(v):
    x = parse_float(v)
    if x is None or x < 0:
        return None
    return x


COMPASS = {d: i * 22.5 for i, d in enumerate(
    "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}


def parse_direction(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in COMPASS:
        return COMPASS[s]
    x = parse_float(s)
    return None if x is None else x % 360.0


CSS_COLOURS = {
    "white": "#f2f2f2", "black": "#262626", "grey": "#808080", "gray": "#808080",
    "lightgrey": "#c8c8c8", "lightgray": "#c8c8c8", "darkgrey": "#505050", "darkgray": "#505050",
    "silver": "#c0c0c0", "red": "#a83232", "darkred": "#6e1e1e", "brown": "#7a5233",
    "maroon": "#6b2626", "orange": "#d9822b", "yellow": "#e3c84b", "beige": "#ddd0b0",
    "tan": "#d2b48c", "green": "#4f7d4f", "darkgreen": "#2f4f2f", "blue": "#3d5f8f",
    "lightblue": "#9cbfd9", "navy": "#24365e", "cyan": "#5fb7c2", "teal": "#2f7d7d",
    "pink": "#d99aa8", "purple": "#6e4a8a", "cream": "#efe6cf", "ivory": "#f5f1e1",
    "gold": "#c9a43a",
}


def parse_colour(v):
    if not v:
        return None
    s = str(v).strip().lower().replace(" ", "")
    if s in CSS_COLOURS:
        s = CSS_COLOURS[s]
    if re.fullmatch(r"#[0-9a-f]{3}", s):
        s = "#" + "".join(c * 2 for c in s[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", s):
        return s
    return None


def hex_to_linear(h):
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return [round(c, 4) for c in lin] + [1.0]


# --------------------------------------------------------------------------------------
# 类型 / 原型
# --------------------------------------------------------------------------------------
RES_TYPES = {"apartments", "residential", "house", "terrace", "detached", "semidetached_house",
             "dormitory", "bungalow", "villa"}
COM_TYPES = {"commercial", "office", "hotel", "retail", "supermarket", "mall", "kiosk", "bank"}
PUB_TYPES = {"school", "hospital", "kindergarten", "university", "college", "church", "cathedral",
             "chapel", "temple", "mosque", "synagogue", "religious", "civic", "public", "government",
             "museum", "train_station", "transportation", "sports_hall", "stadium", "library",
             "theatre", "fire_station", "clinic", "townhall", "pavilion", "sports_centre"}
IND_TYPES = {"industrial", "warehouse", "factory", "manufacture", "storage_tank", "silo"}
MINOR_TYPES = {"roof", "carport", "shed", "hut", "garage", "garages", "kiosk", "toilets", "cabin",
               "service", "container", "construction", "ruins", "bridge", "gatehouse", "guardhouse"}


def type_group(t):
    if t == "yes":
        return "yes"
    if t in RES_TYPES:
        return "res"
    if t in COM_TYPES:
        return "com"
    if t in PUB_TYPES:
        return "pub"
    if t in IND_TYPES:
        return "ind"
    return "other"


ARCHETYPES = {
    # code: (中文名, 墙色, 屋顶色)
    "shikumen":        ("石库门/里弄", "#7f746a", "#4f4643"),
    "lowrise_res":     ("低层住宅",    "#c4b49c", "#5a4c46"),
    "midrise_res":     ("多层住宅",    "#d6cdbd", "#7a7772"),
    "midhigh_res":     ("中高层住宅",  "#d2cdc4", "#76736e"),
    "highrise_res":    ("高层住宅",    "#cfd2d4", "#73767a"),
    "commercial":      ("商业办公",    "#b9bcbf", "#6c6e70"),
    "highrise_office": ("高层办公",    "#7d93a6", "#5d6670"),
    "public":          ("公共建筑",    "#d8c6a6", "#6f6a62"),
    "industrial":      ("工业",        "#a5a59d", "#7a7a74"),
    "canopy":          ("雨棚",        "#9aa0a6", "#8a9096"),
    "other":           ("其他",        "#bdb6aa", "#6f6c68"),
}


def archetype_of(btype, levels, area):
    g = type_group(btype)
    if btype in ("roof", "carport"):
        return "canopy"
    if g == "ind":
        return "industrial"
    if g == "pub":
        return "public"
    if g == "com":
        return "highrise_office" if levels >= 10 else "commercial"
    if g == "other":
        return "other"
    # 住宅类与 yes
    if g == "yes" and area > 2000:
        return "highrise_office" if levels >= 10 else "commercial"
    if btype in ("yes", "residential", "house", "terrace") and levels <= 3 and area < 400:
        return "shikumen"
    if levels <= 3:
        return "lowrise_res"
    if levels <= 6:
        return "midrise_res"
    if levels < 10:
        return "midhigh_res"
    return "highrise_res"


def floor_height(btype, archetype=None):
    if btype == "church" or btype in ("cathedral", "chapel"):
        return 5.0
    if btype in RES_TYPES:
        return 3.0
    if btype in ("office", "commercial", "hotel"):
        return 3.8
    if btype == "retail":
        return 4.5
    if btype == "yes" and archetype is not None:
        if archetype in ("shikumen", "lowrise_res", "midrise_res", "midhigh_res", "highrise_res"):
            return 3.0
        if archetype in ("commercial", "highrise_office"):
            return 3.8
    return 3.3


def rule_levels(btype, area):
    if btype in ("house", "terrace", "detached", "semidetached_house", "bungalow", "villa"):
        return 2
    if btype in ("hut", "shed", "garage", "garages", "kiosk", "toilets", "cabin", "service",
                 "container", "gatehouse", "guardhouse", "construction", "ruins"):
        return 1
    if btype in ("yes", "residential"):
        if area < 250:
            return 2
        if area < 1000:
            return 3
        if area < 2000:
            return 6
        return 5
    if btype == "apartments":
        return 6 if area < 1500 else 12
    if btype in ("commercial", "office", "hotel"):
        return 8
    if btype in ("retail", "supermarket", "mall"):
        return 3
    if btype in ("school", "university", "college"):
        return 4
    if btype in ("hospital", "clinic"):
        return 6
    if btype == "kindergarten":
        return 3
    if type_group(btype) == "ind":
        return 2
    if btype in ("church", "cathedral", "chapel", "temple", "religious"):
        return 2
    if type_group(btype) == "pub":
        return 4
    if type_group(btype) == "res":
        return 6
    return 3


def cap_levels(levels, area):
    if area < 80:
        return min(levels, 3)
    if area < 200:
        return min(levels, 6)
    return levels


ROOF_ALIASES = {
    "flat": "flat", "gabled": "gabled", "saltbox": "gabled", "round": "gabled",
    "hipped": "hipped", "half-hipped": "hipped", "mansard": "hipped", "gambrel": "hipped",
    "side_hipped": "hipped", "pyramidal": "pyramidal", "dome": "pyramidal", "onion": "pyramidal",
    "cone": "pyramidal", "skillion": "skillion", "lean_to": "skillion",
}


# --------------------------------------------------------------------------------------
# 投影
# --------------------------------------------------------------------------------------
class Projector:
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        p = math.radians(lat0)
        self.m_lat = 111132.954 - 559.822 * math.cos(2 * p) + 1.175 * math.cos(4 * p)
        self.m_lon = 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p)

    def fwd(self, lon, lat):
        return ((np.asarray(lon) - self.lon0) * self.m_lon, (np.asarray(lat) - self.lat0) * self.m_lat)

    def inv(self, e, n):
        return (self.lon0 + e / self.m_lon, self.lat0 + n / self.m_lat)


# --------------------------------------------------------------------------------------
# 几何构造
# --------------------------------------------------------------------------------------
def clean_polys(geom):
    """返回 Polygon 列表 (已修复、已统一方向)。"""
    if geom is None or geom.is_empty:
        return []
    if not geom.is_valid:
        geom = make_valid(geom)
    out = []
    stack = [geom]
    while stack:
        g = stack.pop()
        if isinstance(g, Polygon):
            if g.area > 0.5:
                out.append(orient(g, 1.0))
        elif hasattr(g, "geoms"):
            stack.extend(g.geoms)
    return out


def way_lonlat_polygon(coords):
    if len(coords) < 3:
        return None
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    if len(coords) < 4:
        return None
    return Polygon(coords)


def element_lonlat_geom(el):
    """Overpass 'out geom' 元素 -> 经纬度 (lon, lat) 的 shapely 几何。"""
    if el["type"] == "way":
        coords = [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
        p = way_lonlat_polygon(coords)
        if p is None:
            return None
        return p if p.is_valid else make_valid(p)
    outers, inners = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        coords = [(g["lon"], g["lat"]) for g in m["geometry"] if g]
        if len(coords) < 2:
            continue
        (inners if m.get("role") == "inner" else outers).append(LineString(coords))
    if not outers:
        return None
    outer = unary_union([p if p.is_valid else make_valid(p) for p in polygonize(outers)])
    if outer.is_empty:
        return None
    if inners:
        inner = unary_union([p if p.is_valid else make_valid(p) for p in polygonize(inners)])
        outer = outer.difference(inner)
    return outer


# --------------------------------------------------------------------------------------
# 屋顶 / 墙体网格
# --------------------------------------------------------------------------------------
def obb_frame(poly):
    """最小外接矩形 -> 中心 c，长轴 u，短轴 v，半长 A，半宽 B。"""
    rect = poly.minimum_rotated_rectangle
    if not isinstance(rect, Polygon):
        b = poly.bounds
        rect = box(*b) if b[2] > b[0] and b[3] > b[1] else box(b[0], b[1], b[0] + 1, b[1] + 1)
    pts = np.array(rect.exterior.coords[:4])
    e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
    l1, l2 = np.linalg.norm(e1), np.linalg.norm(e2)
    if l1 < 1e-9 or l2 < 1e-9:
        return pts.mean(axis=0), np.array([1.0, 0.0]), np.array([0.0, 1.0]), max(l1, l2, 0.5) / 2, 0.5
    if l1 >= l2:
        u, A, B = e1 / l1, l1 / 2, l2 / 2
    else:
        u, A, B = e2 / l2, l2 / 2, l1 / 2
    v = np.array([-u[1], u[0]])
    c = pts.mean(axis=0)
    return c, u, v, max(A, 0.25), max(B, 0.25)


def roof_planes(poly, shape, direction=None, orientation=None):
    """返回 (平面列表, 坡向跨度信息)。每个平面 f(x,y) = a + bx*x + by*y，值域约 [0,1]。

    屋顶高度 z = 墙顶 + roof_h * min_i f_i。
    """
    c, u, v, A, B = obb_frame(poly)

    def plane_st(k0, ks, kt):
        # f = k0 + ks*s + kt*t, s=(p-c).u, t=(p-c).v
        bx = ks * u[0] + kt * v[0]
        by = ks * u[1] + kt * v[1]
        a = k0 - bx * c[0] - by * c[1]
        return (a, bx, by)

    if shape == "gabled":
        if orientation == "across":
            return [plane_st(1, -1 / A, 0), plane_st(1, 1 / A, 0)], A
        return [plane_st(1, 0, -1 / B), plane_st(1, 0, 1 / B)], B
    if shape == "hipped":
        return [plane_st(1, 0, -1 / B), plane_st(1, 0, 1 / B),
                plane_st(A / B, -1 / B, 0), plane_st(A / B, 1 / B, 0)], B
    if shape == "pyramidal":
        return [plane_st(1, 0, -1 / B), plane_st(1, 0, 1 / B),
                plane_st(1, -1 / A, 0), plane_st(1, 1 / A, 0)], B
    if shape == "skillion":
        if direction is not None:
            th = math.radians(direction)
            d = np.array([math.sin(th), math.cos(th)])  # 指向下坡方向 (东, 北)
        else:
            d = v
        xy = np.array(poly.exterior.coords)
        proj = xy @ d
        pmax, pmin = proj.max(), proj.min()
        L = max(pmax - pmin, 0.5)
        # f = (pmax - p.d)/L
        return [(pmax / L, -d[0] / L, -d[1] / L)], L
    return [(0.0, 0.0, 0.0)], 0.0


def default_roof_height(shape, run):
    if shape in ("gabled", "hipped", "pyramidal"):
        return min(run * math.tan(math.radians(30)), 8.0)
    if shape == "skillion":
        return min(run * math.tan(math.radians(15)), 6.0)
    return 0.0


def halfplane_clip(poly_pts, a, bx, by):
    """Sutherland-Hodgman: 凸多边形与 {a + bx*x + by*y <= 0} 求交。"""
    out = []
    n = len(poly_pts)
    for i in range(n):
        p, q = poly_pts[i], poly_pts[(i + 1) % n]
        gp = a + bx * p[0] + by * p[1]
        gq = a + bx * q[0] + by * q[1]
        if gp <= 0:
            out.append(p)
        if (gp < 0 < gq) or (gq < 0 < gp):
            t = gp / (gp - gq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def plane_regions(poly, planes):
    """每个平面 i 的作用区域 = poly ∩ {f_i <= f_j, ∀j}。"""
    if len(planes) == 1:
        return [[poly]]
    minx, miny, maxx, maxy = poly.bounds
    pad = 10.0
    bb = [(minx - pad, miny - pad), (maxx + pad, miny - pad), (maxx + pad, maxy + pad), (minx - pad, maxy + pad)]
    regions = []
    for i, (ai, bxi, byi) in enumerate(planes):
        pts = bb
        for j, (aj, bxj, byj) in enumerate(planes):
            if i == j:
                continue
            da, dbx, dby = ai - aj, bxi - bxj, byi - byj
            if abs(dbx) < 1e-12 and abs(dby) < 1e-12:
                # 平行平面：只有 f_i 不大于 f_j 时保留 (相等时只保留编号小的)
                if da > 1e-12 or (abs(da) <= 1e-12 and j < i):
                    pts = []
                    break
                continue
            pts = halfplane_clip(pts, da, dbx, dby)
            if len(pts) < 3:
                break
        if len(pts) < 3:
            regions.append([])
            continue
        reg = poly.intersection(Polygon(pts))
        regions.append([orient(p, 1.0) for p in clean_polys_raw(reg)])
    return regions


def clean_polys_raw(g):
    out, stack = [], [g]
    while stack:
        x = stack.pop()
        if isinstance(x, Polygon):
            if x.area > EPS_AREA and not x.is_empty:
                out.append(x)
        elif hasattr(x, "geoms"):
            stack.extend(x.geoms)
    return out


def triangulate(poly):
    """earcut 三角化 (含洞)。返回 (verts Nx2, tris Mx3)。"""
    rings = [np.asarray(poly.exterior.coords)[:-1]]
    for r in poly.interiors:
        rings.append(np.asarray(r.coords)[:-1])
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64)
    verts = np.vstack(rings).astype(np.float64)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    idx = earcut.triangulate_float64(verts, ends)
    return verts, np.asarray(idx, dtype=np.int64).reshape(-1, 3)


class MeshBuf:
    """一个 primitive 的顶点缓冲 (ENU 坐标，单位 m)。"""

    def __init__(self):
        self.pos, self.nrm, self.uv, self.idx = [], [], [], []
        self.n = 0

    def add(self, pos, nrm, uv, tris):
        pos = np.asarray(pos, dtype=np.float64)
        self.pos.append(pos)
        self.nrm.append(np.broadcast_to(np.asarray(nrm, dtype=np.float64), pos.shape).copy())
        self.uv.append(np.asarray(uv, dtype=np.float64))
        self.idx.append(np.asarray(tris, dtype=np.int64) + self.n)
        self.n += len(pos)

    def arrays(self):
        if not self.pos:
            return None
        return (np.vstack(self.pos), np.vstack(self.nrm), np.vstack(self.uv), np.vstack(self.idx))

    def extend(self, other):
        a = other.arrays()
        if a is None:
            return
        p, nr, uv, ix = a
        self.pos.append(p)
        self.nrm.append(nr)
        self.uv.append(uv)
        self.idx.append(ix + self.n)
        self.n += len(p)


def build_piece(poly, base, wall_top, roof_h, shape, fh, direction, orientation, walls, roof):
    """为一个多边形生成墙体和屋顶，写入 walls / roof 两个 MeshBuf。"""
    if roof_h <= 0.01:
        shape = "flat"
    planes, _ = roof_planes(poly, shape, direction, orientation) if shape != "flat" else ([(0.0, 0.0, 0.0)], 0)
    if shape == "flat":
        roof_h = 0.0

    def ztop(x, y):
        return wall_top + roof_h * min(a + bx * x + by * y for a, bx, by in planes)

    # ---- 屋顶 ----
    regions = plane_regions(poly, planes)
    for (a, bx, by), regs in zip(planes, regions):
        kx, ky = roof_h * bx, roof_h * by           # z = z0 + kx*x + ky*y
        z0 = wall_top + roof_h * a
        n = np.array([-kx, -ky, 1.0])
        n /= np.linalg.norm(n)
        for rp in regs:
            v2, tris = triangulate(rp)
            if len(tris) == 0:
                continue
            z = z0 + kx * v2[:, 0] + ky * v2[:, 1]
            p3 = np.column_stack([v2, z])
            # 法线朝上：检查三角形顶点顺序
            e1 = p3[tris[:, 1]] - p3[tris[:, 0]]
            e2 = p3[tris[:, 2]] - p3[tris[:, 0]]
            cz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
            flip = cz < 0
            tris[flip] = tris[flip][:, [0, 2, 1]]
            keep = np.abs(cz) > 1e-5  # 丢弃退化三角形 (转 float32 后面积为 0)
            tris = tris[keep]
            if len(tris) == 0:
                continue
            uv = np.column_stack([v2[:, 0] / 10.0, -v2[:, 1] / 10.0])
            roof.add(p3, n, uv, tris)

    # ---- 底面 (仅抬高部件) ----
    if base > 0.01:
        v2, tris = triangulate(poly)
        if len(tris):
            p3 = np.column_stack([v2, np.full(len(v2), base)])
            e1 = p3[tris[:, 1]] - p3[tris[:, 0]]
            e2 = p3[tris[:, 2]] - p3[tris[:, 0]]
            cz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
            up = cz > 0
            tris[up] = tris[up][:, [0, 2, 1]]
            tris = tris[np.abs(cz) > 1e-5]
            if len(tris):
                uv = np.column_stack([v2[:, 0] / 10.0, -v2[:, 1] / 10.0])
                walls.add(p3, [0.0, 0.0, -1.0], uv, tris)

    # ---- 墙体 ----
    pair_g = [(planes[i][0] - planes[j][0], planes[i][1] - planes[j][1], planes[i][2] - planes[j][2])
              for i in range(len(planes)) for j in range(i + 1, len(planes))]
    rings = [poly.exterior] + list(poly.interiors)
    for ring in rings:
        pts = np.asarray(ring.coords)
        dist = 0.0
        for k in range(len(pts) - 1):
            p, q = pts[k], pts[k + 1]
            d = q - p
            L = math.hypot(d[0], d[1])
            if L < 1e-6:
                continue
            ts = [0.0, 1.0]
            for (ga, gbx, gby) in pair_g:
                g0 = ga + gbx * p[0] + gby * p[1]
                g1 = ga + gbx * q[0] + gby * q[1]
                if (g0 < 0 < g1) or (g1 < 0 < g0):
                    ts.append(g0 / (g0 - g1))
            ts = sorted(set(round(t, 9) for t in ts))
            nrm = np.array([d[1] / L, -d[0] / L, 0.0])
            for t0, t1 in zip(ts[:-1], ts[1:]):
                if (t1 - t0) * L < 1e-3:  # 短于 1 mm 的碎段
                    continue
                a2 = p + t0 * d
                b2 = p + t1 * d
                za, zb = ztop(*a2), ztop(*b2)
                if za - base < 1e-4 and zb - base < 1e-4:
                    continue
                quad = np.array([[a2[0], a2[1], base], [b2[0], b2[1], base],
                                 [b2[0], b2[1], zb], [a2[0], a2[1], za]])
                u0 = (dist + t0 * L) / 3.0
                u1 = (dist + t1 * L) / 3.0
                uv = np.array([[u0, 0.0], [u1, 0.0], [u1, -(zb - base) / fh], [u0, -(za - base) / fh]])
                tris = []
                if zb - base > 1e-4:
                    tris.append([0, 1, 2])
                if za - base > 1e-4:
                    tris.append([0, 2, 3])
                walls.add(quad, nrm, uv, tris)
            dist += L


# --------------------------------------------------------------------------------------
# GLB 写出
# --------------------------------------------------------------------------------------
def enu_to_gltf(a):
    """ENU (东, 北, 天) -> glTF (X=东, Y=上, Z=南)。"""
    return np.column_stack([a[:, 0], a[:, 2], -a[:, 1]])


class GlbWriter:
    def __init__(self):
        self.bin = bytearray()
        self.buffer_views, self.accessors, self.meshes, self.nodes = [], [], [], []
        self.materials, self.mat_index = [], {}

    def _view(self, data, target):
        while len(self.bin) % 4:
            self.bin += b"\x00"
        off = len(self.bin)
        self.bin += data
        self.buffer_views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": target})
        return len(self.buffer_views) - 1

    def _acc(self, arr, ctype, typ, target, minmax=False):
        view = self._view(arr.tobytes(), target)
        acc = {"bufferView": view, "componentType": ctype, "count": int(arr.shape[0]), "type": typ}
        if minmax:
            acc["min"] = [float(x) for x in arr.min(axis=0)]
            acc["max"] = [float(x) for x in arr.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def material(self, name, rgba, roughness=0.85):
        if name not in self.mat_index:
            self.materials.append({"name": name, "pbrMetallicRoughness": {
                "baseColorFactor": rgba, "metallicFactor": 0.0, "roughnessFactor": roughness}})
            self.mat_index[name] = len(self.materials) - 1
        return self.mat_index[name]

    def add_mesh(self, name, prims):
        """prims: [(MeshBuf 数组元组 (ENU, 已减去枢轴), material_index)]"""
        out = []
        for arrs, mat in prims:
            if arrs is None:
                continue
            pos, nrm, uv, idx = arrs
            pos = enu_to_gltf(pos).astype(np.float32)
            nrm = enu_to_gltf(nrm).astype(np.float32)
            uv = uv.astype(np.float32)
            idx = idx.reshape(-1)
            if pos.shape[0] < 65536:
                ind = idx.astype(np.uint16)
                ctype = 5123
            else:
                ind = idx.astype(np.uint32)
                ctype = 5125
            out.append({
                "attributes": {
                    "POSITION": self._acc(pos, 5126, "VEC3", 34962, True),
                    "NORMAL": self._acc(nrm, 5126, "VEC3", 34962),
                    "TEXCOORD_0": self._acc(uv, 5126, "VEC2", 34962),
                },
                "indices": self._acc(ind, ctype, "SCALAR", 34963),
                "material": mat, "mode": 4,
            })
        if not out:
            return None
        self.meshes.append({"name": name, "primitives": out})
        return len(self.meshes) - 1

    def add_node(self, name, mesh, translation=None):
        node = {"name": name, "mesh": mesh}
        if translation is not None:
            node["translation"] = [float(x) for x in translation]
        self.nodes.append(node)

    def write(self, path, scene_name="Scene"):
        gl = {
            "asset": {"version": "2.0", "generator": "huangpu-ue5-model build_huangpu.py",
                      "copyright": "© OpenStreetMap contributors, ODbL"},
            "scene": 0, "scenes": [{"name": scene_name, "nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes, "meshes": self.meshes, "materials": self.materials,
            "accessors": self.accessors, "bufferViews": self.buffer_views,
            "buffers": [{"byteLength": len(self.bin)}],
        }
        js = json.dumps(gl, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        js += b" " * ((4 - len(js) % 4) % 4)
        bn = bytes(self.bin) + b"\x00" * ((4 - len(self.bin) % 4) % 4)
        total = 12 + 8 + len(js) + 8 + len(bn)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(js), 0x4E4F534A))
            f.write(js)
            f.write(struct.pack("<II", len(bn), 0x004E4942))
            f.write(bn)
        return total


# --------------------------------------------------------------------------------------
# 实体处理
# --------------------------------------------------------------------------------------
class Entity:
    __slots__ = ("el", "osm_type", "osm_id", "tags", "lonlat", "polys", "area", "is_part",
                 "parts", "parent", "btype", "levels", "height", "wall_h", "min_h", "roof_shape",
                 "roof_h", "fh", "source", "archetype", "centroid", "data_levels", "geom",
                 "override", "remainder")

    def __init__(self, el, lonlat, polys, is_part):
        self.el = el
        self.osm_type = el["type"]
        self.osm_id = el["id"]
        self.tags = el.get("tags", {})
        self.lonlat = lonlat
        self.polys = polys
        self.geom = MultiPolygon(polys) if len(polys) > 1 else polys[0]
        self.area = sum(p.area for p in polys)
        self.is_part = is_part
        self.parts = []
        self.parent = None
        c = self.geom.centroid
        self.centroid = (c.x, c.y)
        t = self.tags
        bt = t.get("building") if not is_part else (t.get("building") or "yes")
        if is_part and bt in (None, "no"):
            bt = "yes"
        self.btype = (bt or "yes").strip().lower().split(";")[0]
        self.data_levels = None
        self.override = None
        self.remainder = None


def building_id(e):
    return "HP-" + e.osm_type[0] + str(e.osm_id)


def levels_from_height(h, fh, btype):
    """只有高度时推算层数 (仅用于清单展示和原型判断)。'yes' 类高楼多为办公，按 3.8 m 计。"""
    if btype == "yes" and h > 60:
        fh = 3.8
    return max(1, int(round(h / fh)))


def compute_height(e, neighbors_fn):
    """按优先级确定高度与屋顶参数。"""
    t = dict(e.tags)
    pt = e.parent.tags if e.parent is not None else {}
    ov = e.override
    if ov:
        for k_ov, k_tag in (("height_m", "height"), ("levels", "building:levels"),
                            ("roof_shape", "roof:shape"), ("min_height_m", "min_height")):
            if ov.get(k_ov):
                t[k_tag] = ov[k_ov]
        if ov.get("height_m") and not ov.get("levels"):
            t.pop("building:levels", None)
        if ov.get("levels") and not ov.get("height_m"):
            t.pop("height", None)

    h_tag = parse_height(t.get("height"))
    lv_tag = parse_levels(t.get("building:levels"))
    # 部件缺高度时继承所属轮廓的数据
    if e.is_part and h_tag is None and lv_tag is None and e.parent is not None:
        h_tag = parse_height(pt.get("height"))
        lv_tag = parse_levels(pt.get("building:levels"))
    roof_lv = parse_levels(t.get("roof:levels"))
    roof_h_tag = parse_nonneg(t.get("roof:height"))
    min_h = parse_nonneg(t.get("min_height"))
    min_lv = parse_levels(t.get("building:min_level"))
    shape_raw = (t.get("roof:shape") or "").strip().lower()
    shape = ROOF_ALIASES.get(shape_raw) if shape_raw else None
    if shape_raw and shape is None:
        shape = "flat"
    area = e.area
    bt = e.btype

    fh = floor_height(bt)
    if h_tag is not None:
        source = "osm_height"
        levels = lv_tag if lv_tag else levels_from_height(h_tag, fh, bt)
    elif lv_tag is not None:
        source = "osm_levels"
        levels = lv_tag
    else:
        levels = None
        if bt not in MINOR_TYPES:
            levels = neighbors_fn(e)
        if levels is not None:
            source = "neighbor_estimate"
        else:
            source = "rule_estimate"
            levels = rule_levels(bt, area)
        levels = cap_levels(levels, area)

    arch = archetype_of(bt, levels, area)
    fh = floor_height(bt, arch)
    if h_tag is not None and not lv_tag:
        levels = levels_from_height(h_tag, fh, bt)
        arch = archetype_of(bt, levels, area)
        fh = floor_height(bt, arch)

    if shape is None:
        if source != "osm_height" and bt in ("house", "terrace", "residential", "yes") \
                and levels <= 3 and area < 400 and not e.is_part:
            shape = "gabled"
        else:
            shape = "flat"

    # 雨棚
    if bt in ("roof", "carport"):
        top = h_tag if h_tag is not None else 4.0
        base = min_h if min_h is not None else max(top - 0.5, 0.0)
        if base >= top:
            base = max(top - 0.5, 0.0)
        e.levels, e.height, e.min_h, e.roof_shape, e.roof_h = 1, top, base, "flat", 0.0
        e.wall_h, e.fh, e.source, e.archetype = top - base, fh, source, "canopy"
        return

    base = min_h if min_h is not None else (min_lv * fh if min_lv else 0.0)

    # 屋顶高度
    run = roof_run(e.polys, shape, parse_direction(t.get("roof:direction")),
                   (t.get("roof:orientation") or "").lower())
    if shape == "flat":
        roof_h = 0.0
    elif roof_h_tag is not None:
        roof_h = roof_h_tag
    elif roof_lv:
        roof_h = roof_lv * fh
    else:
        roof_h = default_roof_height(shape, run)

    if source == "osm_height":
        total = h_tag
        if base >= total - 0.5:
            base = max(0.0, total - max(roof_h, 0.5) - 0.5) if total > 1.0 else 0.0
        roof_h = min(roof_h, (total - base) / 2.0) if shape != "flat" else 0.0
        wall_top = total - roof_h
    else:
        wall_top = levels * fh  # building:levels 按 OSM 约定从地面起算 (含 min_level 以下的层)
        if shape == "flat" and roof_lv:
            wall_top += roof_lv * fh
            roof_h = 0.0
        if wall_top <= base + 0.5:
            wall_top = base + max(fh, 0.5)
        roof_h = min(roof_h, wall_top - base)
        total = wall_top + roof_h

    if ov and (ov.get("height_m") or ov.get("levels")):
        source = "manual_override"
    e.levels, e.height, e.min_h, e.roof_shape, e.roof_h = levels, total, base, shape, roof_h
    e.wall_h, e.fh, e.source, e.archetype = wall_top - base, fh, source, arch


def make_remainder(e):
    """轮廓里没有被部件覆盖的部分 (常见于只画了塔楼部件的楼)，按裙房建模。

    轮廓自身数据比最高部件低时，认为它描述的就是裙房，直接使用；否则按类型 + 面积套规则，
    且不高于最高部件。
    """
    covered = unary_union([p.geom for p in e.parts])
    rem = e.geom.difference(covered)
    rem = rem.buffer(-0.3, join_style=2).buffer(0.3, join_style=2)  # 去掉细缝
    polys = [p for p in clean_polys(rem) if p.area > 10.0]
    area = sum(p.area for p in polys)
    if area < 20.0 or area < 0.03 * e.area:
        return None
    max_part = max(p.height for p in e.parts)
    tags = {k: v for k, v in e.tags.items() if k in ("building", "building:colour", "roof:colour")}
    oh = parse_height(e.tags.get("height"))
    ol = parse_levels(e.tags.get("building:levels"))
    fh0 = floor_height(e.btype)
    outline_h = oh if oh is not None else (ol * fh0 if ol else None)
    if outline_h is not None and outline_h < max_part - 1.0:
        for k in ("height", "building:levels", "min_height"):
            if k in e.tags:
                tags[k] = e.tags[k]
    el = {"type": e.osm_type, "id": e.osm_id, "tags": tags}
    r = Entity(el, None, polys, True)
    compute_height(r, lambda _e: None)
    if r.height > max_part:
        r.height = max_part
        r.roof_h = 0.0
        r.roof_shape = "flat"
        r.wall_h = max_part - r.min_h
        r.levels = max(1, int(round(r.wall_h / r.fh)))
    return r


def roof_run(polys, shape, direction, orientation):
    if shape == "flat" or not polys:
        return 0.0
    p = max(polys, key=lambda q: q.area)
    _, run = roof_planes(p, shape, direction, orientation)
    return run


def build_entity_mesh(e):
    walls, roof = MeshBuf(), MeshBuf()
    direction = parse_direction(e.tags.get("roof:direction"))
    orientation = (e.tags.get("roof:orientation") or "").lower()
    wall_top = e.min_h + e.wall_h
    for p in e.polys:
        build_piece(p, e.min_h, wall_top, e.roof_h, e.roof_shape, e.fh, direction, orientation, walls, roof)
    return walls, roof


def fmt_address(t):
    if t.get("addr:full"):
        return t["addr:full"]
    street = t.get("addr:street", "")
    hn = t.get("addr:housenumber", "")
    if hn and re.fullmatch(r"[\d\-]+", hn):
        hn += "号"
    s = street + hn
    if t.get("addr:housename"):
        s = (s + " " + t["addr:housename"]).strip()
    return s


def load_overrides(path):
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            bid = (r.get("building_id") or "").strip()
            if bid and not bid.startswith("#"):
                out[bid] = {k: (v or "").strip() for k, v in r.items()}
    print(f"手工修正: {len(out)} 条 ({path})")
    return out


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/huangpu_osm.json")
    ap.add_argument("--output", default="output")
    ap.add_argument("--overrides", default="data/overrides.csv",
                    help="手工修正表 (building_id,height_m,levels,roof_shape,min_height_m,note)")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    els = data["elements"]
    osm_timestamp = data.get("osm3s", {}).get("timestamp_osm_base", "")

    # ---- 分类 ----
    raw = []
    for el in els:
        t = el.get("tags", {})
        if el["type"] not in ("way", "relation"):
            continue
        is_part = "building:part" in t and t.get("building:part") != "no"
        is_bld = "building" in t and t.get("building") != "no"
        if not (is_part or is_bld):
            continue
        if el["type"] == "relation" and t.get("type") not in ("multipolygon", "building", None):
            continue
        g = element_lonlat_geom(el)
        if g is None or g.is_empty:
            continue
        raw.append((el, g, is_part))

    # ---- 原点 ----
    cents = np.array([(g.centroid.x, g.centroid.y) for _, g, _ in raw])
    lon0 = round((cents[:, 0].min() + cents[:, 0].max()) / 2, 4)
    lat0 = round((cents[:, 1].min() + cents[:, 1].max()) / 2, 4)
    proj = Projector(lat0, lon0)

    def to_enu(g):
        return affinity.affine_transform(g, [proj.m_lon, 0, 0, proj.m_lat,
                                             -lon0 * proj.m_lon, -lat0 * proj.m_lat])

    ents, skipped = [], []
    for el, g, is_part in raw:
        polys = clean_polys(to_enu(g))
        if not polys:
            skipped.append(el["type"][0] + str(el["id"]))
            continue
        ents.append(Entity(el, g, polys, is_part))

    overrides = load_overrides(args.overrides)
    for e in ents:
        e.override = overrides.get(building_id(e))
    outlines = [e for e in ents if not e.is_part]
    parts = [e for e in ents if e.is_part]

    # ---- 部件归属 ----
    tree = STRtree([e.geom for e in outlines])
    for p in parts:
        rp = p.geom.representative_point()
        cands = [outlines[i] for i in tree.query(rp) if outlines[i].geom.contains(rp)]
        cands = [c for c in cands if c is not p]
        if cands:
            par = min(cands, key=lambda c: c.area)
            par.parts.append(p)
            p.parent = par
    standalone_parts = [p for p in parts if p.parent is None]
    records = outlines + standalone_parts  # 清单记录

    # ---- 已知层数集合 (邻域推算) ----
    known = []
    for e in outlines:
        lv = parse_levels(e.tags.get("building:levels"))
        if lv is None:
            h = parse_height(e.tags.get("height"))
            if h is not None:
                lv = levels_from_height(h, floor_height(e.btype), e.btype)
        if lv is not None:
            e.data_levels = lv
            known.append(e)
    kxy = np.array([k.centroid for k in known]) if known else np.zeros((0, 2))
    try:
        from scipy.spatial import cKDTree
        ktree = cKDTree(kxy) if len(kxy) else None
    except ImportError:
        ktree = None

    def neighbors_fn(e):
        if not len(kxy):
            return None
        if ktree is not None:
            idx = ktree.query_ball_point(e.centroid, NEIGHBOR_RADIUS)
        else:
            d = np.hypot(kxy[:, 0] - e.centroid[0], kxy[:, 1] - e.centroid[1])
            idx = np.nonzero(d <= NEIGHBOR_RADIUS)[0]
        g = type_group(e.btype)
        vals = []
        for i in idx:
            k = known[i]
            if k is e:
                continue
            r = k.area / max(e.area, 1e-6)
            if not (0.4 <= r <= 2.5):
                continue
            kg = type_group(k.btype)
            if not (g == "yes" or kg == "yes" or kg == g):
                continue
            vals.append(k.data_levels)
        if len(vals) < 3:
            return None
        return int(math.floor(float(np.median(vals)) + 0.5))

    for e in ents:
        compute_height(e, neighbors_fn)

    # ---- 输出目录 ----
    out = args.output
    mdir = os.path.join(out, "models", "buildings")
    tdir = os.path.join(out, "models", "tiles")
    for d in (mdir, tdir):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)

    all_c = np.array([e.centroid for e in records])
    tile_e0 = math.floor(all_c[:, 0].min() / TILE_SIZE) * TILE_SIZE
    tile_n0 = math.floor(all_c[:, 1].min() / TILE_SIZE) * TILE_SIZE

    rows, features = [], []
    tiles = defaultdict(list)
    meshes = {}
    seen_ids = set()
    for e in records:
        bid = building_id(e)
        assert bid not in seen_ids, bid
        seen_ids.add(bid)
        walls, roof = MeshBuf(), MeshBuf()
        if e.parts:
            e.remainder = make_remainder(e)
            pieces = e.parts + ([e.remainder] if e.remainder else [])
            for p in pieces:
                w, r = build_entity_mesh(p)
                walls.extend(w)
                roof.extend(r)
            top = max(p.height for p in pieces)
            base = min(p.min_h for p in pieces)
            src_rank = ["manual_override", "osm_height", "osm_levels", "neighbor_estimate", "rule_estimate"]
            source = min((p.source for p in e.parts), key=src_rank.index)
            tagged = [parse_levels(p.tags.get("building:levels")) for p in e.parts]
            tagged = [x for x in tagged if x]
            lv_out = parse_levels(e.tags.get("building:levels")) or (max(tagged) if tagged else
                                                                     levels_from_height(top, 3.3, "yes"))
            tallest = max(e.parts, key=lambda p: p.height)
            shape_out, roof_h_out, wall_h_out, fh_out = tallest.roof_shape, tallest.roof_h, tallest.wall_h, tallest.fh
            arch = archetype_of(e.btype, lv_out, e.area)
        else:
            w, r = build_entity_mesh(e)
            walls, roof = w, r
            top, base, source, lv_out = e.height, e.min_h, e.source, e.levels
            shape_out, roof_h_out, wall_h_out, fh_out, arch = e.roof_shape, e.roof_h, e.wall_h, e.fh, e.archetype
        if e.parts:
            e.archetype = arch

        wall_col = parse_colour(e.tags.get("building:colour"))
        roof_col = parse_colour(e.tags.get("roof:colour"))
        if e.parts and not wall_col:
            wall_col = next((parse_colour(p.tags.get("building:colour")) for p in e.parts
                             if parse_colour(p.tags.get("building:colour"))), None)
        if e.parts and not roof_col:
            roof_col = next((parse_colour(p.tags.get("roof:colour")) for p in e.parts
                             if parse_colour(p.tags.get("roof:colour"))), None)
        arch_cn, def_wall, def_roof = ARCHETYPES[arch]
        wall_mat = ("MAT_Wall_" + wall_col[1:]) if wall_col else ("MAT_Wall_" + arch)
        if roof_col:
            roof_mat = "MAT_Roof_" + roof_col[1:]
        elif shape_out in ("gabled", "hipped", "pyramidal") and arch in ("shikumen", "lowrise_res"):
            roof_mat = "MAT_Roof_tile"
        elif shape_out != "flat":
            roof_mat = "MAT_Roof_sloped_" + arch
        else:
            roof_mat = "MAT_Roof_flat_" + arch
        wall_rgba = hex_to_linear(wall_col or def_wall)
        roof_rgba = hex_to_linear(roof_col or ("#4f4643" if roof_mat == "MAT_Roof_tile" else def_roof))

        cx, cy = e.centroid
        lon, lat = proj.inv(cx, cy)
        ti = int((cx - tile_e0) // TILE_SIZE)
        tj = int((cy - tile_n0) // TILE_SIZE)
        tile = f"tile_{ti}_{tj}"

        wa, ra = walls.arrays(), roof.arrays()
        if wa is None and ra is None:
            raise RuntimeError("empty mesh " + bid)

        def shift(a):
            if a is None:
                return None
            p, nr, uv, ix = a
            p = p.copy()
            p[:, 0] -= cx
            p[:, 1] -= cy
            return (p, nr, uv, ix)

        local = (shift(wa), shift(ra))
        meshes[bid] = (local, (wall_mat, wall_rgba), (roof_mat, roof_rgba))
        gw = GlbWriter()
        prims = []
        if local[0] is not None:
            prims.append((local[0], gw.material(wall_mat, wall_rgba)))
        if local[1] is not None:
            prims.append((local[1], gw.material(roof_mat, roof_rgba, 0.9)))
        gw.add_node(bid, gw.add_mesh(bid, prims))
        model_file = f"models/buildings/{bid}.glb"
        gw.write(os.path.join(out, model_file), bid)
        tiles[tile].append(bid)

        t = e.tags
        nverts = (0 if wa is None else len(wa[0])) + (0 if ra is None else len(ra[0]))
        ntris = (0 if wa is None else len(wa[3])) + (0 if ra is None else len(ra[3]))
        row = {
            "building_id": bid, "osm_type": e.osm_type, "osm_id": e.osm_id,
            "name": t.get("name") or t.get("name:zh") or "",
            "name_en": t.get("name:en") or "",
            "address": fmt_address(t),
            "building_type": e.btype, "archetype": arch, "archetype_cn": arch_cn,
            "levels": int(lv_out), "height_m": round(top, 2), "wall_height_m": round(wall_h_out, 2),
            "min_height_m": round(base, 2), "roof_shape": shape_out, "roof_height_m": round(roof_h_out, 2),
            "floor_height_m": fh_out, "data_source": source,
            "footprint_area_m2": round(e.area, 1), "has_parts": bool(e.parts), "part_count": len(e.parts),
            "lat": round(lat, 7), "lon": round(lon, 7),
            "east_m": round(cx, 2), "north_m": round(cy, 2),
            "ue_x_cm": round(cx * 100, 1), "ue_y_cm": round(-cy * 100, 1), "ue_z_cm": 0.0,
            "tile": tile, "model_file": model_file, "tile_file": f"models/tiles/{tile}.glb",
            "wall_material": wall_mat, "roof_material": roof_mat,
            "vertex_count": nverts, "triangle_count": ntris,
        }
        rows.append(row)
        features.append({"type": "Feature", "properties": row, "geometry": mapping(e.lonlat)})

    # ---- 分块 / 全区 ----
    def add_to(gw, bid, with_translation):
        local, (wm, wr), (rm, rr) = meshes[bid]
        prims = []
        if local[0] is not None:
            prims.append((local[0], gw.material(wm, wr)))
        if local[1] is not None:
            prims.append((local[1], gw.material(rm, rr, 0.9)))
        mi = gw.add_mesh(bid, prims)
        row = row_by_id[bid]
        gw.add_node(bid, mi, [row["east_m"], 0.0, -row["north_m"]] if with_translation else None)

    row_by_id = {r["building_id"]: r for r in rows}
    tile_sizes = {}
    for tile, bids in sorted(tiles.items()):
        gw = GlbWriter()
        for bid in bids:
            add_to(gw, bid, True)
        tile_sizes[tile] = gw.write(os.path.join(tdir, f"{tile}.glb"), tile)

    all_path = os.path.join(out, "models", "huangpu_all.glb")
    if os.path.exists(all_path):
        os.remove(all_path)
    est = sum(tile_sizes.values())
    all_written = False
    if est < ALL_GLB_LIMIT:
        gw = GlbWriter()
        for r in rows:
            add_to(gw, r["building_id"], True)
        gw.write(all_path, "Huangpu")
        all_written = True

    # ---- 清单 ----
    fields = list(rows[0].keys())
    with open(os.path.join(out, "buildings.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(out, "buildings.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "buildings.geojson"), "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)

    origin = {
        "lat": lat0, "lon": lon0, "datum": "WGS-84 (OSM 原始坐标，不是 GCJ-02)",
        "projection": "local tangent plane (ENU), meters",
        "m_per_deg_lat": round(proj.m_lat, 4), "m_per_deg_lon": round(proj.m_lon, 4),
        "formula": {
            "m_per_deg_lat": "111132.954 - 559.822*cos(2φ) + 1.175*cos(4φ)",
            "m_per_deg_lon": "111412.84*cos(φ) - 93.5*cos(3φ)",
            "east_m": "(lon - lon0) * m_per_deg_lon", "north_m": "(lat - lat0) * m_per_deg_lat",
        },
        "gltf_axes": "X = east, Y = up, Z = south (meters)",
        "ue_axes": "X = east, Y = south, Z = up (centimeters); ue_x_cm = east_m*100, ue_y_cm = -north_m*100",
        "ground_z": 0.0,
        "tile_size_m": TILE_SIZE, "tile_origin_east_m": tile_e0, "tile_origin_north_m": tile_n0,
        "tile_rule": "tile_<i>_<j>: i = floor((east - tile_origin_east)/500), j = floor((north - tile_origin_north)/500)",
        "osm_timestamp": osm_timestamp,
        "attribution": "© OpenStreetMap contributors, ODbL 1.0",
    }
    with open(os.path.join(out, "origin.json"), "w", encoding="utf-8") as f:
        json.dump(origin, f, ensure_ascii=False, indent=2)

    stats = {
        "osm_timestamp": osm_timestamp,
        "elements_total": len(els),
        "outlines": len(outlines), "parts": len(parts),
        "parts_attached": len(parts) - len(standalone_parts), "parts_standalone": len(standalone_parts),
        "outlines_with_parts": sum(1 for e in outlines if e.parts),
        "skipped_invalid_geometry": skipped,
        "records": len(rows), "tiles": len(tiles),
        "data_source": dict(Counter(r["data_source"] for r in rows)),
        "archetype": dict(Counter(r["archetype"] for r in rows)),
        "roof_shape": dict(Counter(r["roof_shape"] for r in rows)),
        "huangpu_all_written": all_written,
        "buildings_with_parts": [
            {"building_id": building_id(e), "name": e.tags.get("name", ""),
             "parts": [{"part": p.osm_type[0] + str(p.osm_id), "min_height_m": round(p.min_h, 1),
                        "height_m": round(p.height, 1), "roof_shape": p.roof_shape,
                        "data_source": p.source} for p in e.parts],
             "podium_remainder": None if not e.remainder else {
                 "area_m2": round(e.remainder.area, 1), "height_m": round(e.remainder.height, 1),
                 "data_source": e.remainder.source}}
            for e in outlines if e.parts],
        "tiles_total_bytes": est,
        "total_triangles": sum(r["triangle_count"] for r in rows),
    }
    with open(os.path.join(out, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    if not args.no_preview:
        preview(records, rows, tile_e0, tile_n0, tiles, os.path.join(out, "preview_map.png"))


def preview(records, rows, e0, n0, tiles, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection
        from matplotlib.patches import Patch, Rectangle
    except ImportError:
        print("matplotlib 未安装，跳过预览图")
        return
    colors = {"osm_height": "#d62728", "osm_levels": "#ff7f0e",
              "neighbor_estimate": "#1f77b4", "rule_estimate": "#9e9e9e"}
    by_src = defaultdict(list)
    for e, r in zip(records, rows):
        for p in e.polys:
            by_src[r["data_source"]].append(np.asarray(p.exterior.coords))
    fig, ax = plt.subplots(figsize=(14, 14), dpi=150)
    for tile in tiles:
        i, j = map(int, tile.split("_")[1:])
        ax.add_patch(Rectangle((e0 + i * TILE_SIZE, n0 + j * TILE_SIZE), TILE_SIZE, TILE_SIZE,
                               fill=False, lw=0.3, ec="#cccccc"))
    for src in ["rule_estimate", "neighbor_estimate", "osm_levels", "osm_height"]:
        if by_src[src]:
            ax.add_collection(PolyCollection(by_src[src], facecolors=colors[src], edgecolors="none"))
    counts = Counter(r["data_source"] for r in rows)
    ax.legend(handles=[Patch(color=colors[s], label=f"{s} ({counts.get(s, 0)})") for s in colors],
              loc="upper left", fontsize=11)
    ax.autoscale()
    ax.set_aspect("equal")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.set_title(f"Huangpu District buildings: {len(rows)} models, colored by data_source "
                 f"(500 m tiles)  © OpenStreetMap contributors")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())
