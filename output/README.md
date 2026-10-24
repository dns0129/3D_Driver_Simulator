# 上海市黄浦区全量建筑模型（供 UE5 使用）

本目录是由 `scripts/build_huangpu.py` 从 OpenStreetMap 数据自动生成的黄浦区建筑白模，共 **7039 栋**。每栋楼对应一个 `.glb` 模型，楼和模型的对应关系写在 `buildings.csv` / `buildings.json` / `buildings.geojson` 三份清单里。

- 数据时间：OSM 快照 2026-10-24T05:20:49Z（Overpass API）
- 有真实数据的楼按数据建模，没有数据的楼做"通用建模"：占地轮廓用真实数据，高度和屋顶按建筑类型和周边建筑推算，并标注所属的通用原型
- 数据许可：**© OpenStreetMap contributors，ODbL 1.0**。使用时必须署名

## 1. 数据来源统计

| data_source | 含义 | 栋数 | 占比 |
|---|---|---:|---:|
| `osm_height` | OSM 里有 `height` 标签（屋顶最高点），按真实高度建模 | 37 | 0.5% |
| `osm_levels` | OSM 里有 `building:levels`，按层数 × 层高建模 | 1005 | 14.3% |
| `neighbor_estimate` | 无数据；取 150 m 内同类、面积相近（0.4–2.5 倍）且至少 3 栋已知层数的楼，用它们层数的中位数 | 1088 | 15.5% |
| `rule_estimate` | 无数据；按类型和占地面积套规则（见第 5 节） | 4909 | 69.7% |
| `manual_override` | 用户在 `data/overrides.csv` 里手工修正的楼（当前 0 栋） | 0 | 0% |

**约 85% 的楼高是推算值。** 30 栋楼带 `building:part` 分体部件，共 74 个部件（世茂广场、明天广场等），这些楼按部件建模。

原型分布：石库门/里弄 2968、多层住宅 1592、低层住宅 1252、中高层住宅 373、高层住宅 367、商业办公 301、公共建筑 77、高层办公 67、其他 15、雨棚 14、工业 13。屋顶：平顶 4052、双坡 2969、四坡 15、单坡 3。

`preview_map.png` 是按 data_source 着色的俯视图：外滩、人民广场、南京东路一带数据较全；老城厢、董家渡、打浦桥以南大多是推算值。

## 2. 目录结构

```
output/
  README.md                      本说明
  origin.json                    原点经纬度、投影参数、坐标约定、分块规则
  buildings.csv                  清单，UTF-8 BOM（Excel 直接打开不乱码），每栋楼一行
  buildings.json                 同上，JSON 格式
  buildings.geojson              原始轮廓（经纬度）+ 全部字段，可用 QGIS 等 GIS 软件查看
  stats.json                     统计信息，以及每栋部件楼的部件明细
  validation.json                自动校验结果（第 9 节）
  preview_map.png                俯视预览图
  models/buildings/<building_id>.glb   单栋模型（7039 个），枢轴在占地质心的地面
  models/tiles/tile_<i>_<j>.glb        500 m × 500 m 分块（94 块），每栋楼一个命名节点，已带世界坐标
  models/huangpu_all.glb               全区合并为一个文件（约 20 MB），已带世界坐标
  ue5/import_huangpu.py          UE5 编辑器 Python 批量导入脚本
```

## 3. 坐标系

- **原点**：所有建筑质心包围盒的中心，取 4 位小数：**北纬 31.2196°，东经 121.4779°**（WGS-84）。地面 z = 0（黄浦区地势平坦，没有做地形）。
- **投影**：以原点为切点的局部切平面（ENU，东/北/天，单位 m），φ 为原点纬度：
  - 每度纬度的米数 = 111132.954 − 559.822·cos2φ + 1.175·cos4φ = 110873.26 m
  - 每度经度的米数 = 111412.84·cosφ − 93.5·cos3φ = 95284.78 m
  - `east_m = (lon − 121.4779) × 95284.78`，`north_m = (lat − 31.2196) × 110873.26`
  - 全区范围在 ±3 km 内，误差为厘米级。
- OSM 坐标是 **WGS-84，不是 GCJ-02（火星坐标）**。和高德/百度底图叠加会有约 500 m 的偏移。
- **glTF 坐标**：X = 东，Y = 上，Z = 南（右手系，单位 m）。
- **UE 坐标**：X = 东，Y = 南，Z = 上（单位 cm）。UE 的 glTF 导入器会把 (x, y, z) 换成 (x, z, y) 并乘 100，所以清单里：
  - `ue_x_cm = east_m × 100`
  - `ue_y_cm = −north_m × 100`
  - `ue_z_cm = 0`
- 单栋模型的枢轴在**占地质心的地面**，放到 (ue_x_cm, ue_y_cm, 0) 即可。分块文件和全区文件里的节点已经带了世界位置。

## 4. 清单字段（buildings.csv / buildings.json）

| 字段 | 说明 |
|---|---|
| `building_id` | 编号 = `HP-` + OSM 类型首字母（w = way，r = relation）+ OSM ID，如 `HP-w10726438`。重新生成时保持不变，模型文件名与它相同 |
| `osm_type` / `osm_id` | OSM 原始对象，可在 `https://www.openstreetmap.org/way/<osm_id>` 查看 |
| `name` / `name_en` | 楼名（中文 / 英文），共 596 栋有名字 |
| `address` | 地址（街道 + 门牌），共 258 栋有地址 |
| `building_type` | OSM 的 `building` 值（yes、apartments、house、commercial 等） |
| `archetype` / `archetype_cn` | 通用原型代码 / 中文名（第 6 节） |
| `levels` | 层数（数据值或推算值；只有高度时按层高反推） |
| `height_m` | 总高度（地面到屋顶最高点） |
| `wall_height_m` | 墙体高度（从 min_height 到檐口） |
| `min_height_m` | 底部抬高（连廊、悬挑部件、雨棚等，平常为 0） |
| `roof_shape` / `roof_height_m` | 屋顶形状（flat / gabled / hipped / pyramidal / skillion）/ 屋顶高度 |
| `floor_height_m` | 层高（同时也是墙面 UV 纵向 1 个单位对应的高度） |
| `data_source` | 高度来源（第 1 节） |
| `footprint_area_m2` | 占地面积 |
| `has_parts` / `part_count` | 是否由 building:part 部件组成 / 部件数（部件楼的高度字段取最高部件） |
| `lat` / `lon` | 占地质心经纬度（WGS-84） |
| `east_m` / `north_m` | 占地质心局部坐标（m） |
| `ue_x_cm` / `ue_y_cm` / `ue_z_cm` | UE 世界坐标（cm），即模型在关卡里的摆放位置 |
| `tile` / `tile_file` | 所属 500 m 分块 / 分块文件 |
| `model_file` | 单栋模型文件（相对于 output/） |
| `wall_material` / `roof_material` | 模型里的材质槽名（可在 UE 里按名字批量替换） |
| `vertex_count` / `triangle_count` | 顶点数 / 三角形数 |

分块编号规则：`i = floor((east_m + 2500) / 500)`，`j = floor((north_m + 3000) / 500)`。

## 5. 高度推算规则（优先级从高到低）

1. **osm_height**：直接用 `height`（屋顶最高点）。屋顶高度不超过总高的一半，墙高 = 总高 − 屋顶高。
2. **osm_levels**：墙高 = `building:levels` × 层高；有 `roof:levels` 时再加屋顶层；坡屋顶的高度加在墙顶之上。
3. **neighbor_estimate**：150 m 内已知层数的楼，要求占地面积比在 0.4–2.5 之间、类型组相同（`yes` 可匹配任意组），至少 3 栋时取它们层数的中位数。
4. **rule_estimate**：
   - `house` / `terrace`：2 层，双坡屋顶；`hut` / `shed`：1 层；`roof`（雨棚）：只做 0.5 m 厚的板，底面离地 3.5 m
   - `yes` / `residential`：面积 < 250 m² 为 2 层（石库门/里弄，双坡屋顶）；250–1000 m² 为 3 层；1000–2000 m² 为 6 层；> 2000 m² 为 5 层（裙房或商场）
   - `apartments`：面积 < 1500 m² 为 6 层，否则 12 层
   - `commercial` / `office` / `hotel` 8 层；`retail` 3 层；`school` 4 层；`hospital` 6 层；`kindergarten` 3 层；`industrial` 2 层；`church` 2 层（层高 5 m）
- **面积上限**（只作用于推算值）：面积 < 80 m² 最多 3 层，< 200 m² 最多 6 层，防止把小房子推成高楼。
- **层高**：住宅类 3.0 m；办公、商业、酒店 3.8 m；零售 4.5 m；其余 3.3 m（`yes` 按推算出的原型取 3.0 或 3.8 m）。
- **通用屋顶**：没有 `roof:shape` 时，`house` / `terrace` / `residential` / `yes` 类、≤ 3 层且面积 < 400 m² 的楼用双坡屋顶（里弄坡屋顶），其余用平顶。默认坡度：双坡/四坡 30°（最高 8 m），单坡 15°（最高 6 m）。
- **脏数据容错**：`building:levels=0` 按缺失处理；`14.` 取 14；`8;6` 取第一个数 8；高度带单位时用正则取第一个数（ft 会换算成米）。

### 部件楼（building:part）

- 部件的代表点落在某个轮廓内时，归入包含它的最小轮廓。部件楼**不重复生成外轮廓**，只把各部件合并进这栋楼的模型。
- 部件缺高度时继承所属轮廓的数据；轮廓也没有数据时按规则推算。
- OSM 里很多楼只画了塔楼部件，没画裙房（如世茂广场部件只覆盖轮廓的 12%）。脚本会把"轮廓减去部件"剩下的部分作为**裙房**补上：轮廓自身的数据低于最高部件时，认为它描述的就是裙房，直接使用；否则按类型和面积套规则推算，且不高于最高部件。补裙房的明细见 `stats.json` 的 `buildings_with_parts[].podium_remainder`。

## 6. 通用原型

| archetype | 中文 | 判定 | 栋数 | 默认墙色 |
|---|---|---|---:|---|
| `shikumen` | 石库门/里弄 | yes / residential / house / terrace，≤ 3 层，面积 < 400 m² | 2968 | 灰砖 #7f746a，瓦顶 |
| `lowrise_res` | 低层住宅 | 住宅类 ≤ 3 层 | 1252 | #c4b49c |
| `midrise_res` | 多层住宅 | 4–6 层 | 1592 | #d6cdbd |
| `midhigh_res` | 中高层住宅 | 7–9 层 | 373 | #d2cdc4 |
| `highrise_res` | 高层住宅 | ≥ 10 层 | 367 | #cfd2d4 |
| `commercial` | 商业办公 | 商业类 < 10 层，或面积 > 2000 m² 的 yes | 301 | #b9bcbf |
| `highrise_office` | 高层办公 | 商业类 ≥ 10 层 | 67 | 玻璃蓝 #7d93a6 |
| `public` | 公共建筑 | 学校、医院、教堂、政府等 | 77 | #d8c6a6 |
| `industrial` | 工业 | industrial / warehouse 等 | 13 | #a5a59d |
| `canopy` | 雨棚 | roof / carport | 14 | #9aa0a6 |
| `other` | 其他 | 其余类型 | 15 | #bdb6aa |

OSM 里有 `building:colour` / `roof:colour` 时优先使用（材质名会变成 `MAT_Wall_<色值>`）。

## 7. 网格、UV 与材质

- 每栋楼 1 个 mesh，含 2 个 primitive（墙、屋顶），在 UE 里就是 **1 个 StaticMesh、2 个材质槽**。
- 墙面每个四边形独立顶点，屋顶每个坡面独立顶点，保证硬边和平直着色。属性：POSITION、NORMAL、TEXCOORD_0、索引。
- **墙面 UV**：u = 沿周长累计距离 / 3 m，v = 离墙底的高度 / 层高，即**纵向 1 个 UV 单位 = 1 层**，可直接平铺"一层一格"的窗户贴图（v 已取反，贴图正立）。
- **屋顶 UV**：u = 东 / 10 m，v = 北 / 10 m。
- 材质只有颜色（PBR baseColor，金属度 0，粗糙度 0.85–0.9），没有贴图。材质名按原型复用，如 `MAT_Wall_shikumen`、`MAT_Roof_tile`、`MAT_Roof_flat_midrise_res`，方便在 UE 里按名字或原型批量替换。
- 全部文件已用 Khronos 官方 glTF Validator 抽检（单栋、分块、全区共 45 个文件）：0 错误，0 警告。

## 8. 导入 UE5

> ⚠️ **本数据未在真实 UE 中验证。** 建议先只导入一个地标核对方向和高度：外滩中心 `HP-w10726438`，高 198.9 m，位置 ue = (49 410, −165 714, 0) cm，即原点以东 494 m、以北 1657 m。核对方法：外滩中心的顶应在 Z = 19 890 cm；在 UE 顶视图里，外滩那排楼在国际饭店（ue_x = −103 912）的东边（+X 方向），沿黄浦江呈南北走向，并且两者都在原点北侧（UE 的 −Y 方向）。

### 方式 A：分块直接拖进关卡（最快，位置自动正确）

1. 新建空关卡（或 World Partition 关卡）。
2. *File → Import Into Level...*，选择 `models/tiles/tile_<i>_<j>.glb`（可多选），或者直接导入 `models/huangpu_all.glb`。
3. 导入器会为每个节点生成一个 StaticMeshActor，名称就是 `building_id`，位置已带好。

### 方式 B：用脚本按清单导入（每栋一个资产，带名字、tag 和文件夹）

1. 启用插件 **Python Editor Script Plugin**（UE 5.1+ 默认使用 Interchange glTF 导入器）。
2. 打开 `ue5/import_huangpu.py`，修改顶部配置：
   - `OUTPUT_DIR`：本 output 目录的绝对路径
   - `DEST = "/Game/Huangpu"`：资产目标目录
   - `TILES = None`（或者只导入几个分块，如 `["tile_4_7"]`）、`LIMIT`、`NAME_FILTER`（先填 `"外滩中心"` 测试）、`ARCHETYPES`
   - `YAW_OFFSET = 0`、`FLIP_Y = False`：导入后方向不对时的兜底开关
   - `WALL_MATERIAL` / `ROOF_MATERIAL`：统一替换材质；`ARCHETYPE_WALL_MATERIALS` / `ARCHETYPE_ROOF_MATERIALS`：按原型替换
3. *Output Log* 切到 *Python*，执行：
   ```python
   exec(open(r"D:/huangpu/output/ue5/import_huangpu.py", encoding="utf-8").read())
   ```
4. 脚本流程：读 `buildings.csv` → `AssetImportTask`（automated、save、replace_existing）导入 glb 到 `/Game/Huangpu/Meshes/<tile>/` → `EditorActorSubsystem.spawn_actor_from_object` 摆到 (ue_x_cm, ue_y_cm, ue_z_cm) → actor 标签设为楼名（没有楼名就用编号），tags = [building_id, archetype, data_source]，放进大纲文件夹 `Huangpu/<tile>`。
5. 有进度条，可以取消；已导入的资产和已摆放的 actor 会跳过，**支持断点续跑**。每 200 栋保存一次。

### 与真实地理位置对齐（可选）

用 Cesium for Unreal 时，把 `CesiumGeoreference` 的原点设为 `origin.json` 里的 **纬度 31.2196、经度 121.4779、高度 0**，模型就会和 Cesium 地形或 Google Photorealistic 3D Tiles 对齐（两者都是 WGS-84）。

### 按原型批量换材质

脚本给每个 actor 打了 `archetype` tag。在大纲里按 tag 过滤，或者在 `ARCHETYPE_WALL_MATERIALS` 里配置，例如：
```python
ARCHETYPE_WALL_MATERIALS = {"shikumen": "/Game/Mat/MI_RedBrick", "highrise_office": "/Game/Mat/MI_Glass"}
ARCHETYPE_ROOF_MATERIALS = {"shikumen": "/Game/Mat/MI_Tile"}
```

## 9. 校验结果（`scripts/validate_output.py` → `validation.json`）

| 地标 | 编号 | 期望 | 清单 | 模型包围盒 |
|---|---|---:|---:|---:|
| 外滩中心 | HP-w10726438 | 198.9 m | 198.9 | 198.9 ✅ |
| 上海世茂广场（5 部件 + 裙房） | HP-w51317372 | 333 m | 333.0 | 333.0 ✅ |
| 明天广场（19 部件，12 个单坡部件组成塔顶） | HP-w445161727 | 284.6 m | 284.6 | 284.6 ✅ |
| 香港新世界大厦 | HP-w65254415 | 242 m | 242.0 | 242.0 ✅ |
| 来福士广场 | HP-w445860578 | 222 m | 222.0 | 222.0 ✅ |
| 市政大厦 | HP-w369317530 | 157.8 m | 157.8 | 157.8 ✅ |
| 创兴金融中心 | HP-w369342602 | 174 m | 174.0 | 174.0 ✅ |

- ✅ 部件楼不重复生成外轮廓，30 栋部件楼的模型高度都等于最高部件，明天广场塔冠部件的 min_height = 160.8 m 正确
- ✅ 全部 7039 个模型：无 NaN、三角形数 > 0、无退化三角形、屋顶法线全部朝上（y > 0）、带符号体积为正（法线朝外）、模型高度和清单一致（误差 < 0.5 m）
- ✅ 清单行数 = 模型文件数 = 7039；每个 model_file / tile_file 都存在；building_id 不重复
- ✅ 预览图目视检查无漏块、无飞到远处的楼（全部在原点 ±3 km 内）
- ⚠️ 未在 UE5 中实测导入方向

## 10. 手工修正（overrides）

推算值不准的楼可以在 `data/overrides.csv` 里手工修正后重新生成，例如国际饭店 `HP-w369342509` 在 OSM 里没有高度数据，目前按规则推算为 18 m（实际约 84 m）：

```csv
building_id,height_m,levels,roof_shape,min_height_m,note
HP-w369342509,83.8,24,,,国际饭店
```

`building_id` 可以写楼的编号，也可以写部件的编号（如 `HP-w524255214`）。修正过的楼 `data_source = manual_override`。

## 11. 重新生成

```bash
pip install numpy shapely mapbox_earcut scipy matplotlib trimesh
# 1) 下载数据（必须带 User-Agent，否则 overpass-api.de 返回 406；主站繁忙时换镜像）
curl -s -m 600 -A "huangpu-ue5-model/1.0" --data-urlencode "data@data/query.overpassql" \
     https://overpass-api.de/api/interpreter -o data/huangpu_osm.json
#    镜像：https://maps.mail.ru/osm/tools/overpass/api/interpreter 、https://overpass.kumi.systems/api/interpreter
# 2) 建模（约 15 秒）
python3 scripts/build_huangpu.py
# 3) 校验
python3 scripts/validate_output.py
```

`building_id` 由 OSM ID 决定，重跑后保持不变（OSM 上被删除或重画的楼除外）。

## 12. 已知局限

- **OSM 覆盖不全**：只有约 7000 栋，少于黄浦区真实建筑数量，老城厢等片区有些楼没有勾画出来。OSM 里没有轮廓的楼无法建模。
- **约 85% 的楼高是推算值**，只适合城市尺度的大体量场景（如驾驶模拟的远景和街区体量），不是精确的数字孪生。一些没有数据的地标也会被推算得偏低，可用第 10 节的方法修正。
- 只有体块和简单屋顶，不含地形、立面细节和贴图。如需真实外观，可叠加 Google Photorealistic 3D Tiles 或 Cesium，或按原型在 UE 里配置材质。
- 部件楼的裙房是推算的（第 5 节）。
- 数据许可：© OpenStreetMap contributors，ODbL 1.0。公开发布时必须署名；如果发布的是修改后的数据库，需要按 ODbL 共享。
