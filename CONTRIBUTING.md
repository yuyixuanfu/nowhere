# Contributing to Nowhere

## 开发环境

```bash
# 克隆仓库
git clone <repo-url>
cd nowhere

# 安装依赖
pip install -e ".[dev]"

# 运行测试
pytest nowhere/tests/ -x -q

# 启动试玩模式
python -m nowhere.playground
```

## 写作风格铁律

`describe.py` 里的所有场景文件必须遵守：

1. **禁止空程度词** — 不用"很"、"非常"、"十分"、"巨大"、"美丽"
2. **短句** — 每句不超过 20 字
3. **第二人称现在时** — "你走在路上"，不是"我走在路上"
4. **体感优先** — 温度、触感、声音、气味，不是判断
5. **时间在流** — 加入时间感："过了几秒"、"太阳矮了一截"
6. **不替 AI 感受** — 只描述身体感受，不写"你很开心"、"你很累"

## 添加新场景

### 场景文件格式

```
# 文件头注释
场景描述1
场景描述2
```

### 生物群落标签

场景文件按 biome 过滤：
- `scene_grassland.txt` — 草地
- `scene_desert.txt` — 沙漠
- `scene_tundra.txt` — 冻土
- `scene_forest.txt` — 森林
- `scene_urban.txt` — 城市
- `scene_coast.txt` — 海岸
- `scene_mountain.txt` — 山地

### 季节场景

`seasonal.txt` 格式：
```
[城市|季节] 描述
[京都|春] 樱花从枝头落下来，粉色的雪。
```

### 遇见场景

`encounters.txt` 格式：
```
[region] 描述
[asia] 牦牛站在路中间。
```

Region 标签：`polar`, `africa`, `asia`, `americas`, `europe`, `art`, `natural`

## 添加新地标

在 `places_patch.json` 中添加：
```json
{
  "地标名": {
    "lat": 39.916,
    "lon": 116.397,
    "type": "宫殿"
  }
}
```

## 添加新食物

在 `data/food_by_country.json` 中添加国家条目，或在 `data/food.txt` 中添加：
```
食物名：描述
热干面：芝麻酱裹在面上，你拌了三下才匀。
```

## 测试

```bash
# 运行所有测试
pytest nowhere/tests/ -x -q

# 测试特定模块
pytest nowhere/tests/test_describe.py -x -q

# 试玩模式
python -m nowhere.playground
```

## 提交规范

- 场景文件：`feat(scenes): 添加XX场景`
- Bug修复：`fix(server): 修复XX问题`
- 文档：`docs: 更新README`
- 测试：`test: 添加XX测试`
