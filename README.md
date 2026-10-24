# 3D Driver Simulator：上海黄浦区建筑模型

从 OpenStreetMap 自动生成上海市黄浦区全部 7039 栋建筑的 glTF/GLB 模型，供 UE5 场景使用。

- `data/`：Overpass 查询语句、OSM 原始数据快照 (2026-10-24)、手工修正表 `overrides.csv`
- `scripts/build_huangpu.py`：建模脚本（OSM → GLB + 清单）
- `scripts/validate_output.py`：自动校验
- `output/`：生成结果（模型、清单、UE5 导入脚本），**详细说明见 [output/README.md](output/README.md)**

数据 © OpenStreetMap contributors，ODbL 1.0。
