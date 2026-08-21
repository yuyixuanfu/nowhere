# 乌有乡细颗粒度审计裁决书

**日期**: 2026-08-21
**扫描器**: S1-S9 全量
**审计宪法**: 审缝不审点，修根因不修条目

---

## 一、严重度汇总

| 严重度 | 数量 | 定义 |
|--------|------|------|
| **S1 事实错/破沉浸** | 5 | 立刻修 |
| **S2 错配** | 18 | 本周修 |
| **S3 瑕疵** | 30 | 批量修 |
| **S4 打磨** | 19 | 量化后进内容卡 |

---

## 二、S1 级发现（立刻修）

### 1. `places.db` 0字节——全部地名查询降级
- **位置**: `nowhere/data/places.db`（0 bytes）
- **影响**: `places.nearby()`、`places.find()` 全部返回空，所有地名功能瘫痪
- **根因**: 数据库文件为空壳，需重新运行 `tools/import_geonames.py` 导入
- **扫描器**: S9

### 2. `is_polar_day` 只覆盖北半球——南半球极昼漏判
- **位置**: `describe.py:2649`
- **代码**: `is_polar_day = (phase == "day" and abs(lat) > 60 and month and 4 <= month <= 8)`
- **影响**: 南纬70度1月（实际极昼）被判为非极昼，极昼场景被过滤
- **根因**: 硬编码北半球月份范围，缺南半球分支
- **扫描器**: S6, S5

### 3. `_matches` 的 `polar_day: True` 约束完全无效
- **位置**: `describe.py:476-478`
- **代码**: 只处理了 `polar_day: False`（排除极昼），缺少 `polar_day: True`（只在极昼时启用）的反向判断
- **影响**: scene_meta.json 中 requires `polar_day: True` 的场景在非极昼条件下也能通过
- **扫描器**: S5

### 4. 地表中文名 4 份互相矛盾
- **位置**: `describe.py:1209` `_SURFACE_DESC` vs `describe.py:2256` `_SURFACE_ZH` vs `soundscape.py:20` vs `server.py:6325`
- **影响**: 同一个 surface type 在不同上下文显示不同中文名（如 snow="积雪"/"雪原"/"雪地"/"雪"）
- **6/11 个 key 翻译不一致**
- **扫描器**: S7

### 5. `_pick_scene` keyword过滤后meta约束失效
- **位置**: `describe.py:570-571`
- **代码**: `if meta and len(meta) == len(filtered)` — keyword过滤删减了pool长度，导致 `len(meta) != len(filtered)`，meta约束被整体跳过
- **影响**: 经过沙漠/热带/冬季关键词过滤后的场景失去structured meta精确约束
- **扫描器**: S5

---

## 三、S2 级发现（本周修）

### 6. `content.pools()` 定义了但从未被调用
- **位置**: `content.py:51-55`
- **扫描器**: S2

### 7. `water_features_scenes.json` 4个字段未使用
- **位置**: segment 的 `culture`, `flora`, `fauna`, `along` 字段
- **影响**: 每个segment都有丰富内容，运行时只取 `scene`，其余4个key全部浪费
- **扫描器**: S2

### 8. `scene_meta.json` 缺少 `ocean` key
- **位置**: `_SURFACE_TO_SCENE["water_ocean"] = "ocean"`，但 scene_meta.json 无 "ocean" key
- **影响**: `_pick_scene("ocean", ...)` 时 meta 约束被跳过
- **扫描器**: S2

### 9. `scene_meta.json` 的 `water` key 实际未被使用
- **位置**: `_render_water()` 在有 SST 数据时直接 return None，不走 `_pick_scene`
- **扫描器**: S2

### 10. `cards.py` 5个迁移函数未清理
- **位置**: `cards.py:387,438,481,490,499` — `migrate_localcolor/humanities/encounters/people/errands()`
- **影响**: 一次性迁移工具完成后未删除，占代码空间
- **扫描器**: S1

### 11. `walk.py` 重复函数
- **位置**: `walk.py:111` `water_body_ahead_km()` — 已被 `water_ahead_km()` 替代，从未调用
- **扫描器**: S1

### 12-16. 未被调用的函数（5个）
- `water.py:130` `describe_current()`
- `people.py:168` `get_person_by_place()`
- `placememory.py:283,288` `load_travelers()`, `save_travelers()`
- `poster.py:185` `cleanup_orphans()`
- **扫描器**: S1

### 17. `_SMELL_BY_SURFACE` 在 `_render_terrain` 中不可达
- **位置**: `describe.py:1972` — 先查 `_SMELL_BY_BIOME`，查不到时 fallback 仍查 `_SMELL_BY_BIOME`（非 `_SMELL_BY_SURFACE`）
- **影响**: `_SMELL_BY_SURFACE` 中的 4 个 surface 气味描述永远不会被走路渲染使用
- **扫描器**: S7

### 18. `_SMELL_BY_BIOME` key 混用 surface/biome
- **位置**: `describe.py:2289` — 包含 `snow`, `water`（surface类型）与 `rainforest`, `desert`（biome类型）混在一起
- **影响**: surface="snow" + biome="tundra" 时两个key都有值，选中哪个取决于调用路径
- **扫描器**: S7

### 19. `_SMELL_BY_SURFACE["water"]` 海洋词误用
- **位置**: `describe.py:2300` — "海风里的咸味" 用于通用 `water` 类型（含淡水湖/河）
- **扫描器**: S4

### 20. `_SMELL_BY_BIOME` 全部11个key仅3条
- **位置**: `describe.py:2289-2301`
- **影响**: 池厚度 < 5，不达标
- **扫描器**: S3

### 21. `_REGION_MAP` 覆盖空隙
- **位置**: `describe.py:247` — 南极洲(lat<-60)、南大洋(lat -60~-50)、中太平洋(lon<-90)等区域不属于任何region
- **影响**: 这些区域的场景元素无法获得region标签，失去地域特色
- **扫描器**: S5

### 22. `_TROPICAL_SEASON` 按北半球定义
- **位置**: `describe.py:189-191` — "summer"="湿季" 是北半球映射，南半球雨林靠巧合正确
- **扫描器**: S6

### 23. `describe.py` 5个global context变量无线程保护
- **位置**: `_CURRENT_BIOME`, `_CURRENT_SEASON`, `_CURRENT_LAT`, `_RECENT_TOUCH`, `_RECENT_SCENES`
- **影响**: 并发请求互相覆盖，描写内容可能错乱
- **扫描器**: S8

---

## 四、S3 级发现（批量修）

### 池厚度不达标（<5条）

| 池/文件 | 当前值 | 需要 |
|---------|--------|------|
| `scene_water_dock.txt` | 1行 | ≥5 |
| `scene_water_ocean.txt` | 1行 | ≥5 |
| `scene_water_stream.txt` | 4行 | ≥5 |
| `_SMELL_BY_BIOME` (全部11键) | 3条/键 | ≥5 |
| `scene_elements.json` terrain_change | 3条/biome | ≥5 |

### 近重复变体（19对）

高优先级：
- `_WATER_WARM_VARIANTS`: 8条中12对重复（"温的/暖的/温吞吞"反复出现）
- `_WEATHER_DELTA_VARIANTS`: 仅调换语序不算新变体
- `_TERRAIN_FLAT_WATER_VARIANTS`: "水面平得像镜子" vs "水平如镜"
- `_SKY_NIGHT_VARIANTS`: "头顶是夜" vs "抬头是夜"
- `_LIFE_VARIANTS`: "有人见过它。它也许还在" vs "有人见过。它还在这里的某处"

### 私有函数标记为废弃但仍存在
- `describe.py:1271` `_feels_clause()` — docstring标DEPRECATED但未删除
- `server.py:293,1647,5350` 3个私有函数未被调用

### 绝对断言（25处HIGH）
- `scene_museum.txt:43` "星月夜前面永远有二十个人"
- `scene_world_enhanced.txt:81` "衣服永远不会干"
- `localcolor.json` 多处 "永远""总是""从不"
- `knowledge.json` 多处 "永远""所有"

---

## 五、S4 级发现（打磨）

### `_BIOME_TO_SCENE` 缺失映射
- `forest`, `grassland` 无 biome→scene 映射（通过 surface 路径覆盖）
- `island` 映射到 `ocean`（可能丢失内陆岛屿特色）

### `_BIOME_TO_SEASONAL_PLACE` 缺 `island`
- island 共用 coast 的 "海岸"，无法区分

### `scene_card_meta.json` biomes 不完整
- 只有7个: coast, mountain, any, forest, tundra, city, desert
- 缺: grassland, island, rainforest, volcano, wetland

### 极夜硬编码月份范围
- `describe.py:887-893` — 对不同纬度精度不足

---

## 六、病因归并（根因→修卡）

| 根因 | 影响条目 | 修卡 |
|------|----------|------|
| **D1 写了没接线** | S1:10个函数 + S2:4个未用字段 | 清理死代码卡 |
| **D2 元数据写了没人读** | S2: pools() + water_features 4字段 | 接线或删除卡 |
| **D3 池薄→数学必然** | S3: 3个txt + SMELL_BY_BIOME + terrain_change | 扩池卡 |
| **D4 全球池无地理维** | S5: REGION_MAP空隙 + biome缺失 | 补地理卡 |
| **D5 同病多函数** | S7: SURFACE_DESC 4份 + 国家码2份 | 统一真相源卡 |
| **D6 双真相源** | S7: SURFACE_DESC vs SURFACE_ZH | 合并卡 |
| **D7 绝对断言卡** | S4: 25处HIGH绝对断言 | 改写卡 |
| **D8 纬度≠气候** | S6: is_polar_day南半球 + TROPICAL_SEASON | 半球修正卡 |
| **D9 子串/模糊匹配当判断** | S8: 4处中风险in匹配 | 精确匹配卡 |
| **D10 占位/凑整值** | S9: places.db 0字节 + works=0 | 数据修复卡 |

---

## 七、修卡优先级排序

1. **🔴 places.db 重建** — 阻塞所有地名功能
2. **🔴 is_polar_day 南半球修正** — describe.py:2649
3. **🔴 _matches polar_day:True 补全** — describe.py:476-478
4. **🔴 _pick_scene meta约束修复** — describe.py:570-571
5. **🟡 SURFACE_ZH 统一** — 4份→1份，其他文件import
6. **🟡 死代码清理** — 10个未调用函数 + 5个迁移函数
7. **🟡 扩池** — 3个txt + SMELL_BY_BIOME + terrain_change
8. **🟡 近重复变体重写** — WATER_WARM + WEATHER_DELTA + LIFE
9. **🟢 绝对断言改写** — 25处HIGH
10. **🟢 全局变量线程安全** — 5个context变量
11. **🟢 REGION_MAP 补洞** — 南极洲等区域
12. **🟢 water_features 未用字段** — 接线或删除

---

*按宪法第4条：修根因不修条目。以上71条发现归并为12张修卡。*
