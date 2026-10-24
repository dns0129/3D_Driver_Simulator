# -*- coding: utf-8 -*-
"""UE5 编辑器脚本：按 buildings.csv 批量导入黄浦区建筑并摆放到正确位置。

运行方式 (UE5 编辑器，需启用 Python Editor Script Plugin)：
    Output Log -> 左下角切到 "Python" -> 输入
        exec(open(r"D:/huangpu/output/ue5/import_huangpu.py", encoding="utf-8").read())
    或 File -> Execute Python Script... 选择本文件。

先只导入一个地标核对方向和高度 (见 README)：把 NAME_FILTER 设成 "外滩中心"，LIMIT 设成 1。

注意：本脚本未在真实 UE 中运行验证，API 以 UE 5.1+ (Interchange glTF 导入器) 为准。
"""
import csv
import math
import os

import unreal

# ======================= 配置 =======================
OUTPUT_DIR = r"C:/path/to/output"   # 改成 output 目录 (含 buildings.csv 的那个目录)
DEST = "/Game/Huangpu"              # 资产导入目标目录
TILES = None                        # None = 全部；或者只导入部分分块，如 ["tile_4_7", "tile_5_7"]
LIMIT = None                        # None = 不限；调试时可设 1 / 50
NAME_FILTER = None                  # 只导入名称或编号包含该字符串的楼，如 "外滩中心" / "HP-w10726438"
ARCHETYPES = None                   # 只导入某些原型，如 ["shikumen", "highrise_office"]
YAW_OFFSET = 0.0                    # 整体绕原点旋转 (度)，兜底用
FLIP_Y = False                      # 导入后南北颠倒时设为 True (位置 Y 取反、模型 Y 方向镜像)
WALL_MATERIAL = None                # 统一替换墙体材质，如 "/Game/Materials/M_Wall"
ROOF_MATERIAL = None                # 统一替换屋顶材质
ARCHETYPE_WALL_MATERIALS = {}       # 按原型替换墙体材质，如 {"shikumen": "/Game/Materials/MI_Shikumen"}
ARCHETYPE_ROOF_MATERIALS = {}       # 按原型替换屋顶材质
SAVE_EVERY = 200                    # 每导入多少栋保存一次
# ===================================================


def log(msg):
    unreal.log("[Huangpu] " + str(msg))


def read_rows():
    path = os.path.join(OUTPUT_DIR, "buildings.csv")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if TILES:
        tiles = set(TILES)
        rows = [r for r in rows if r["tile"] in tiles]
    if NAME_FILTER:
        rows = [r for r in rows if NAME_FILTER in r["name"] or NAME_FILTER in r["building_id"]
                or NAME_FILTER in r["name_en"]]
    if ARCHETYPES:
        a = set(ARCHETYPES)
        rows = [r for r in rows if r["archetype"] in a]
    if LIMIT:
        rows = rows[:int(LIMIT)]
    return rows


def find_static_mesh(paths, folder, bid):
    for p in paths or []:
        a = unreal.EditorAssetLibrary.load_asset(str(p).split(".")[0])
        if isinstance(a, unreal.StaticMesh):
            return a
    # 兜底：在目标目录里按名称找
    for p in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False):
        name = str(p).split("/")[-1].split(".")[0]
        if bid.replace("-", "_") in name.replace("-", "_"):
            a = unreal.EditorAssetLibrary.load_asset(str(p).split(".")[0])
            if isinstance(a, unreal.StaticMesh):
                return a
    return None


def import_glb(glb_path, folder):
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", glb_path)
    task.set_editor_property("destination_path", folder)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    task.set_editor_property("replace_existing", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    try:
        return list(task.get_editor_property("imported_object_paths"))
    except Exception:
        return []


def load_mat(path):
    if not path:
        return None
    m = unreal.EditorAssetLibrary.load_asset(path)
    if m is None:
        unreal.log_warning("[Huangpu] 找不到材质 " + path)
    return m


def apply_materials(actor, wall_mat, roof_mat):
    comp = actor.static_mesh_component
    names = [str(n) for n in comp.get_material_slot_names()]
    for i, n in enumerate(names):
        if wall_mat is not None and "Wall" in n:
            comp.set_material(i, wall_mat)
        elif roof_mat is not None and "Roof" in n:
            comp.set_material(i, roof_mat)
    # 槽名不可识别时按顺序：0 = 墙，1 = 屋顶
    if not any(("Wall" in n or "Roof" in n) for n in names):
        if wall_mat is not None and len(names) > 0:
            comp.set_material(0, wall_mat)
        if roof_mat is not None and len(names) > 1:
            comp.set_material(1, roof_mat)


def world_location(r):
    x = float(r["ue_x_cm"])
    y = float(r["ue_y_cm"])
    z = float(r["ue_z_cm"])
    if FLIP_Y:
        y = -y
    if YAW_OFFSET:
        a = math.radians(YAW_OFFSET)
        x, y = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
    return unreal.Vector(x, y, z)


def main():
    rows = read_rows()
    log("待处理 %d 栋" % len(rows))
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    # 断点续跑：关卡里已有带编号 tag 的 actor 就跳过
    placed = set()
    for a in actor_sub.get_all_level_actors():
        for t in a.tags:
            s = str(t)
            if s.startswith("HP-"):
                placed.add(s)
    log("关卡中已存在 %d 栋，将跳过" % len(placed))

    wall_all, roof_all = load_mat(WALL_MATERIAL), load_mat(ROOF_MATERIAL)
    arch_wall = {k: load_mat(v) for k, v in ARCHETYPE_WALL_MATERIALS.items()}
    arch_roof = {k: load_mat(v) for k, v in ARCHETYPE_ROOF_MATERIALS.items()}

    done = failed = 0
    with unreal.ScopedSlowTask(len(rows), "导入黄浦区建筑") as slow:
        slow.make_dialog(True)
        for i, r in enumerate(rows):
            if slow.should_cancel():
                log("用户取消")
                break
            bid = r["building_id"]
            slow.enter_progress_frame(1, "%s %s (%d/%d)" % (bid, r["name"], i + 1, len(rows)))
            if bid in placed:
                continue
            folder = "%s/Meshes/%s" % (DEST, r["tile"])
            mesh = None
            # 已导入的资产跳过导入
            existing = unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False) \
                if unreal.EditorAssetLibrary.does_directory_exist(folder) else []
            if any(bid.replace("-", "_") in str(p).split("/")[-1].replace("-", "_") for p in existing):
                mesh = find_static_mesh([], folder, bid)
            if mesh is None:
                glb = os.path.join(OUTPUT_DIR, r["model_file"]).replace("\\", "/")
                if not os.path.exists(glb):
                    unreal.log_warning("[Huangpu] 文件不存在 " + glb)
                    failed += 1
                    continue
                mesh = find_static_mesh(import_glb(glb, folder), folder, bid)
            if mesh is None:
                unreal.log_warning("[Huangpu] 导入失败 " + bid)
                failed += 1
                continue

            rot = unreal.Rotator(roll=0.0, pitch=0.0, yaw=float(YAW_OFFSET))
            actor = actor_sub.spawn_actor_from_object(mesh, world_location(r), rot)
            if actor is None:
                failed += 1
                continue
            if FLIP_Y:
                actor.set_actor_scale3d(unreal.Vector(1.0, -1.0, 1.0))
            actor.set_actor_label(r["name"] or bid)
            actor.tags = [unreal.Name(bid), unreal.Name(r["archetype"]), unreal.Name(r["data_source"])]
            actor.set_folder_path("Huangpu/" + r["tile"])
            apply_materials(actor,
                            arch_wall.get(r["archetype"]) or wall_all,
                            arch_roof.get(r["archetype"]) or roof_all)
            done += 1
            if done % SAVE_EVERY == 0:
                unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    log("完成：新摆放 %d 栋，失败 %d 栋" % (done, failed))


main()
