# 乌有乡全量审计裁决书（第二轮）

**日期**: 2026-08-21
**维度**: S1-S18（内容质量 + 代码工程 + 安全/性能/架构）
**审计宪法**: 审缝不审点，修根因不修条目

---

## 一、全量数字

| 维度 | 问题数 | 已修 | 剩余 |
|------|--------|------|------|
| S1 死代码 | 11 | 7 | 4 |
| S2 元数据对账 | 4 | 1 | 3 |
| S3 池厚度 | 8 | 5 | 3 |
| S4 绝对断言 | 40 | 9 | 31 |
| S5 选择器逻辑 | 5 | 2 | 3 |
| S6 时间一致性 | 3 | 1 | 2 |
| S7 双真相源 | 4 | 3 | 1 |
| S8 匹配模式 | 8 | 1 | 7 |
| S9 数据体检 | 3 | 2 | 1 |
| S10 安全 | 1 | 0 | 1 |
| S11 性能 | 3 | 0 | 3 |
| S12 状态管理 | 3 | 0 | 3 |
| S13 架构耦合 | 3 | 0 | 3 |
| S14 确定性 | 2 | 0 | 2 |
| S18 边界条件 | 2 | 0 | 2 |
| **合计** | **100** | **31** | **69** |

---

## 二、剩余问题分类

### 🔴 高优先级（功能性/安全）

| # | 维度 | 问题 | 位置 |
|---|------|------|------|
| 1 | S13 | server.py 6844行巨型模块，33处global，最大函数540行 | server.py |
| 2 | S13 | actions↔server 循环依赖，actions 导入 server 私有符号 | actions.py/server.py |
| 3 | S8 | 5个per-call global变量无线程安全，并发时描写互相污染 | describe.py:1348-1358 |
| 4 | S8 | 51处except+pass（server.py占23处），异常被静默吞掉 | 全项目 |
| 5 | S14 | radio.py:205 random.shuffle用全局random，不可复现 | radio.py:205 |
| 6 | S11 | geocode.py _offline_lookup每次全量扫描8MB文件 | geocode.py:51-83 |

### 🟡 中优先级（架构债/数据浪费）

| # | 维度 | 问题 | 位置 |
|---|------|------|------|
| 7 | S2 | water_features_scenes.json 的 culture/flora/fauna/along 4字段从未读取 | describe.py:2976 |
| 8 | S2 | content.pools() 死API，定义了零调用 | content.py:51 |
| 9 | S1 | 4个死函数未清理（_find_once, _get_lat_band_for_situation, _has_kana, _body_alt_deg） | 各模块 |
| 10 | S5 | _REGION_MAP 分类偏差（Nepal→east_asia, Svalbard→europe） | describe.py:245 |
| 11 | S5 | _BIOME_TO_SCENE 缺 forest/grassland（有 _SURFACE_TO_SCENE 兜底） | describe.py:606 |
| 12 | S6 | _TROPICAL_SEASON 恰好正确但靠巧合，缺注释 | describe.py:201 |
| 13 | S11 | providers._cache 无大小上限，只有TTL懒删除 | providers.py:38 |
| 14 | S12 | places.py/content.py SQLite连接永不关闭 | places.py/content.py |
| 15 | S18 | MCP入口缺lat/lon范围校验 | server.py |
| 16 | S7 | scene_soundscape.txt 布宜诺斯艾利斯条目重复（行150 vs 行846） | scene_soundscape.txt |
| 17 | S14 | knowledge.py/sky.py 使用全局random（需检查是否在叙事路径上） | knowledge.py/sky.py |

### 🟢 低优先级（打磨）

| # | 维度 | 问题 | 位置 |
|---|------|------|------|
| 18 | S4 | scene_*.txt 仍有20处"所有"绝对断言（多为文学修辞） | 各scene文件 |
| 19 | S4 | localcolor.json 仍有19处绝对断言 | localcolor.json |
| 20 | S3 | scene_water_dock/ocean/waterfall 仅6行（偏薄但可接受） | data/ |
| 21 | S3 | 所有_VARIANTS统一8条，循环感风险 | describe.py |
| 22 | S18 | CJK正则[一-鿿]不覆盖扩展区 | describe.py:44 |
| 23 | S9 | terrain.py GRID_URL 指向 mengrru/nowhere（已修正为真实仓库） | terrain.py:24 |
| 24 | S9 | dem.py:32 "placeholder" 出现在docstring中（描述fill value检测，非待办） | dem.py:32 |

---

## 三、已修清单（本轮31项）

### 第一轮修（8张卡）
1. ✅ places.nearby 防崩（places.py OperationalError 兜底）
2. ✅ is_polar_day 南半球修正（describe.py:2652）
3. ✅ _matches polar_day:True 约束（describe.py:488）
4. ✅ _pick_scene meta约束修复（describe.py:582）
5. ✅ SURFACE_ZH 统一导入（soundscape.py/server.py → describe）
6. ✅ 10个死函数清理（cards.py×5, walk.py, describe.py, water.py, people.py, placememory.py×2, poster.py）
7. ✅ _SMELL_BY_BIOME 扩到5条/biome + terrain_change 扩到5-6条/biome
8. ✅ 近重复变体重写（WATER_WARM/WEATHER_DELTA/LIFE）

### 第二轮修（7张卡）
9. ✅ water biome 海风误用→淡水气味
10. ✅ 冬词表单字→多字（三处统一）
11. ✅ scene_meta.json 补 ocean key
12. ✅ server.py 去重 SURFACE_DESC_SERVER + 国家码映射
13. ✅ 三个薄水文文件扩池（dock/ocean/stream）
14. ✅ terrain.py placeholder URL 修正
15. ✅ bare except 加日志（describe.py 2处）

### 第三轮修
16. ✅ server.py sections变量先用后定义
17. ✅ places.db 重建（1233万地名）
18. ✅ 4个死函数清理（server.py×2, cards.py×2）

### 绝对断言改写（9处）
19-27. ✅ scene_museum.txt, scene_world_enhanced.txt×2, describe.py×2, localcolor.json×4

---

## 四、修卡优先级排序（剩余69项）

### 第一批（架构重构，需人工决策）
1. server.py 拆分（core/handlers/compose）
2. actions↔server 循环依赖解除
3. describe.py 5个global变量改为参数传递或threading.local

### 第二批（可机械执行）
4. geocode.py _offline_lookup 改为首次构建内存索引
5. radio.py random.shuffle 改用 rng 参数
6. providers._cache 加大小上限
7. MCP入口加 lat/lon clamp
8. 4个死函数删除

### 第三批（批量打磨）
9. scene_soundscape.txt 去重复条目
10. 绝对断言剩余31处批量改写
11. _REGION_MAP 补 Nepal/Svalbard 分类
12. _TROPICAL_SEASON 加注释

---

*按宪法第4条：修根因不修条目。100条发现归并为12张修卡。已修31项，剩余69项。*
