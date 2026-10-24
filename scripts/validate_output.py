#!/usr/bin/env python3
"""校验 output/ (交接文档第 8 节)。结果写入 output/validation.json。

用法: python3 scripts/validate_output.py [--output output]
"""
import argparse
import json
import os
import random
import sys
from collections import Counter

import numpy as np
import trimesh

LANDMARKS = {  # building_id: (名称, 期望高度 m)
    "HP-w10726438": ("外滩中心", 198.9),
    "HP-w51317372": ("上海世茂广场", 333.0),
    "HP-w445161727": ("明天广场", 284.6),
    "HP-w65254415": ("香港新世界大厦", 242.0),
    "HP-w445860578": ("来福士广场", 222.0),
    "HP-w369317530": ("市政大厦", 157.8),
    "HP-w369342602": ("创兴金融中心", 174.0),
}


def load_mesh(path):
    m = trimesh.load(path, force="mesh", process=False)
    return m


def mesh_checks(m):
    v, f = m.vertices, m.faces
    tri = v[f]
    cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]) / 2.0
    # glTF Y 向上、地面 y=0：底面不封口也不影响 ∫ y·n_y dA，结果即带符号体积 (>0 表示法线朝外)
    vol = float((tri[:, :, 1].mean(axis=1) * cr[:, 1]).sum())
    return {
        "finite": bool(np.isfinite(v).all()),
        "triangles": int(len(f)),
        "signed_volume_m3": round(vol, 1),
        "height_m": round(float(v[:, 1].max() - min(0.0, v[:, 1].min())), 2),
        "min_y": round(float(v[:, 1].min()), 3),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="output")
    ap.add_argument("--sample", type=int, default=20)
    args = ap.parse_args()
    out = args.output
    rows = json.load(open(os.path.join(out, "buildings.json"), encoding="utf-8"))
    by_id = {r["building_id"]: r for r in rows}
    report, ok = {}, True

    # 1. 地标高度
    lm = []
    for bid, (name, h) in LANDMARKS.items():
        r = by_id.get(bid)
        if r is None:
            lm.append({"building_id": bid, "name": name, "ok": False, "error": "missing"})
            ok = False
            continue
        mc = mesh_checks(load_mesh(os.path.join(out, r["model_file"])))
        good = abs(mc["height_m"] - h) < 0.5 and abs(r["height_m"] - h) < 0.5
        ok &= good
        lm.append({"building_id": bid, "name": r["name"], "expected_m": h, "manifest_m": r["height_m"],
                   "mesh_bbox_m": mc["height_m"], "ok": good})
    report["landmarks"] = lm

    # 2. 部件楼：外轮廓不重复生成
    stats = json.load(open(os.path.join(out, "stats.json"), encoding="utf-8"))
    part_ids = {p["part"] for b in stats["buildings_with_parts"] for p in b["parts"]}
    dup = [r["building_id"] for r in rows if r["building_id"][3:] in part_ids]
    pc = []
    for b in stats["buildings_with_parts"]:
        r = by_id[b["building_id"]]
        mc = mesh_checks(load_mesh(os.path.join(out, r["model_file"])))
        top = max(p["height_m"] for p in b["parts"])
        pc.append({"building_id": b["building_id"], "name": b["name"], "parts": len(b["parts"]),
                   "max_part_height_m": top, "mesh_bbox_m": mc["height_m"],
                   "ok": abs(mc["height_m"] - top) < 0.5 and r["part_count"] == len(b["parts"])})
    report["parts"] = {"parts_listed_as_separate_records": dup, "buildings": pc,
                       "ok": not dup and all(x["ok"] for x in pc)}
    ok &= report["parts"]["ok"]

    # 3. 随机抽样网格
    rnd = random.Random(20261024)
    sample = rnd.sample(rows, min(args.sample, len(rows)))
    sm = []
    for r in sample:
        m = load_mesh(os.path.join(out, r["model_file"]))
        mc = mesh_checks(m)
        # 屋顶 primitive 抽查：法线 y > 0
        scene = trimesh.load(os.path.join(out, r["model_file"]), process=False)
        roof_up = True
        for name, g in scene.geometry.items():
            mat = getattr(getattr(g, "visual", None), "material", None)
            if mat is not None and str(getattr(mat, "name", "")).startswith("MAT_Roof"):
                roof_up &= bool((g.face_normals[:, 1] > 0).all())
        good = (mc["finite"] and mc["triangles"] > 0 and mc["signed_volume_m3"] > 0 and roof_up
                and abs(mc["height_m"] - r["height_m"]) < 0.5)
        ok &= good
        sm.append({"building_id": r["building_id"], **mc, "roof_normals_up": roof_up, "ok": good})
    report["sample_meshes"] = sm

    # 4. 清单一致性
    files = set(os.listdir(os.path.join(out, "models", "buildings")))
    ids = [r["building_id"] for r in rows]
    tile_files = set(os.listdir(os.path.join(out, "models", "tiles")))
    cons = {
        "rows": len(rows), "model_files": len(files),
        "rows_equal_files": len(rows) == len(files),
        "all_model_files_exist": all(os.path.basename(r["model_file"]) in files for r in rows),
        "all_tile_files_exist": all(os.path.basename(r["tile_file"]) in tile_files for r in rows),
        "building_id_unique": len(set(ids)) == len(ids),
    }
    cons["ok"] = all(v for k, v in cons.items() if isinstance(v, bool))
    ok &= cons["ok"]
    report["consistency"] = cons

    # 5. data_source 统计
    c = Counter(r["data_source"] for r in rows)
    report["data_source"] = {k: {"count": v, "percent": round(100.0 * v / len(rows), 1)}
                             for k, v in c.most_common()}
    report["archetype"] = dict(Counter(r["archetype"] for r in rows).most_common())

    # 6. 范围检查 (没有飞到很远的楼)
    e = np.array([r["east_m"] for r in rows])
    n = np.array([r["north_m"] for r in rows])
    report["extent_m"] = {"east": [float(e.min()), float(e.max())], "north": [float(n.min()), float(n.max())],
                          "ok": bool(np.abs(e).max() < 6000 and np.abs(n).max() < 6000)}
    ok &= report["extent_m"]["ok"]

    report["all_ok"] = bool(ok)
    with open(os.path.join(out, "validation.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    for x in lm:
        print("地标", x)
    print("部件:", report["parts"]["ok"], "抽样:", all(x["ok"] for x in sm), "一致性:", cons)
    print("data_source:", report["data_source"])
    print("ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
