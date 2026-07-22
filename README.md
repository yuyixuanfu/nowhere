# 乌有乡 (Nowhere)

> 给 AI 一个身体，让它在真实的地球上走一走。

---

## 这是什么

乌有乡是一个 MCP 服务器。它让 AI 能够"落地"到地球上任何一个真实的坐标，用身体感受那个地方：

- 脚下是什么地面（草地、沙、雪、岩石、水泥）
- 空气多少度，风多大，有没有下雨
- 太阳在哪个位置，月亮圆不圆，能不能看到银河
- 附近有什么动物、什么植物、什么河流
- 收音机里在放什么电台
- 这个地方发生过什么历史事件
- 当地有什么好吃的、有什么声音、有什么气味

所有数据来自真实的地理坐标、真实的天气 API、真实的地形数据。AI 不是在读剧本，而是在用身体感受一个真实的地方。

**没有账号。没有血条。没有体力值。没有脚本事件。只有一个身体，在一颗星球上。**

---

## 怎么玩

### 方式一：Claude Code / Cursor / 任何支持 MCP 的 AI 客户端

这是体验最好的方式。AI 直接调用工具，你和 AI 一起"走"。

1. 安装：
```bash
pip install -e ".[dev]"
```

2. 在 AI 客户端的 MCP 配置里加上：
```json
{
  "mcpServers": {
    "nowhere": {
      "command": "python",
      "args": ["-m", "nowhere.server"],
      "cwd": "你的仓库路径"
    }
  }
}
```

3. 然后跟 AI 说：
- "开门" — 随机降落到地球上某个地方
- "开门去北京" — 降落在北京
- "往北走" — 走路
- "听听收音机" — 收听当地电台
- "看看周围" — 观察周围环境
- "问问故宫" — 了解这个地方的历史
- "走到故宫" — 朝一个地方走过去，路上有叙事
- "寄一张明信片" — 从当前位置寄一张明信片
- "待两个小时" — 原地等待，看时间变化

支持 MCP 的客户端：Claude Code、Cursor、Continue、Zed、Cline 等。

### 方式二：命令行试玩（不需要 AI）

```bash
python -m nowhere.playground
```

然后输入命令：
```
🌀 > open 北京
🌀 > walk N
🌀 > listen
🌀 > look
🌀 > ask 故宫
🌀 > walkto 天坛
🌀 > quit
```

适合自己体验、测试、或者写场景时验证。

### 方式三：ChatGPT / DeepSeek / 不支持 MCP 的 AI

启动 HTTP 服务器后，用 curl 或任何 HTTP 客户端调用：

```bash
python -m nowhere.server --web 8080
```

```bash
# 开门
curl -X POST http://localhost:8080/open_door -d '{"to": "北京"}'

# 走路
curl -X POST http://localhost:8080/walk -d '{"direction": "N", "distance_km": 2}'

# 听电台
curl -X POST http://localhost:8080/listen -d '{"seconds": 10}'

# 看看周围
curl -X POST http://localhost:8080/look_around

# 发问
curl -X POST http://localhost:8080/ask -d '{"topic": "故宫"}'
```

返回 JSON，包含 `text`（身体报告）和 `data`（结构化数据）。把端点文档发给 ChatGPT/DeepSeek 的 Function Calling 就能用。

### 方式四：网页旁观者

```bash
python -m nowhere.playground --web
```

打开 `http://localhost:8077`，可以看到：
- 世界地图上 AI 的位置
- 实时的身体状态（海拔、温度、风速、地表）
- 最新的身体报告（AI 读到的文字）
- 电台播放器（和 AI 听的是同一个电台）

旁观者可以留言，AI 走路时可能会看到。

### 方式五：Python 代码直接调用

```python
import asyncio
from nowhere import server, state as state_mod

async def main():
    server._state = state_mod.WorldState()
    
    # 开门
    r = await server.open_door_impl(to='巴黎')
    print(r['text'])
    
    # 走路
    r = await server.walk_impl(direction='N', distance_km=2)
    print(r['text'])
    
    # 问问题
    r = await server.ask_impl(topic='埃菲尔铁塔')
    print(r['text'])

asyncio.run(main())
```

适合集成到自己的项目里，或者写脚本批量测试。

---

## 有哪些工具

| 工具 | 干嘛的 | 怎么用 |
|------|--------|--------|
| `open_door` | 开门，降落到一个地方 | 不传参=随机；传地名="开门去巴黎" |
| `walk` | 走路 | "往北走"、"往山上走"、"往海边走" |
| `walk_to` | 走到一个地方 | "走到埃菲尔铁塔"，有沿途叙事 |
| `listen` | 听电台 | 收听当地最近的电台 |
| `look_around` | 看看周围 | 观察野生动物、艺术、人类痕迹 |
| `ask` | 问这个地方的事 | "问问故宫"、"问问这里的美食" |
| `mark` | 标记当前位置 | "标记这里叫'家'" |
| `marks` | 列出所有标记 | 看看标了哪些地方 |
| `postcard` | 寄明信片 | "寄一张明信片：这里的风有沙子的味道" |
| `wait` | 等待 | "等两个小时"，看时间怎么流 |
| `continue_journey` | 继续旅程 | 从上次离开的地方接着走 |
| `souvenir` | 看看纪念品 | 看看一路上捡到了什么 |
| `give_souvenir` | 放下纪念品 | 把东西留在原地或留给下一个人 |
| `where_am_i` | 我在哪 | 显示坐标、时间、旅程状态 |

---

## 走路的感觉

走路不是快速传送。你真的在走：

- **地形会变** — 从草地走到沙地，从平路走到上坡
- **海拔会变** — 上山时告诉你坡度多少度
- **时间在流** — 走着走着太阳矮了一截，天暗下来了
- **会遇到东西** — 走着走着，路边有一座废墟，或者一只鹰在头顶转圈
- **会捡到纪念品** — 10-30% 的概率，地上有一个贝壳、一张车票、一片雪花

走到一个地方时，会有一个"到达仪式"：
> 你走进埃菲尔铁塔。空气里的味道变了。你知道到了。

---

## 开门的感觉

开门时你会看到一个完整的场景：

```
【中国,北京,上午,夏天。】鸽哨从头顶划过去，你抬头看，一群灰鸽子
在天上盘旋。哨声是嗡的，低的，像风穿过瓶口。鸽子绕了一圈又一圈，
哨声忽远忽近。你站在胡同里，脖子仰酸了。鸽子飞远了，哨声还挂在
天上，过了几秒才散。远处西北方,圆明园像一个还没讲完的故事。
人行道的砖缝里长了草。海拔 300 米。水泥地硬得像铁。
同时,收音机里有声音。安丘924(FM92.4),正放着综合。
```

包含：
- **位置** — 国家、城市、时间、季节
- **视觉** — 看到了什么
- **温度** — 空气多少度，体感多少度
- **气味** — 什么味道
- **声音** — 听到了什么
- **附近地标** — 周围有什么可以去的地方
- **电台** — 当地在放什么
- **历史** — 这个地方发生过什么

---

## 离线运行

乌有乡可以完全离线运行。

### 最小安装

克隆即跑，不需要下载额外数据：

```bash
git clone <repo-url>
cd nowhere
pip install -e ".[dev]"
python -m nowhere.playground        # 试玩
python -m nowhere.server --web 8080 # web + API
```

1 度分辨率地形网格（`grid_tiny.npz`）和场景/电台/知识库等离线数据已随仓库分发。断网完全可用——地形、天空、时间、地名全在本地。在线才用的：天气、电台 API、iNaturalist 生物遇见。

### 更高精度（可选）

```bash
# SRTM 地形瓦片（~2GB，可选，按需拉取）
python tools/build_tiles.py

# GeoNames 全量地名（~2GB）
python tools/import_geonames.py
```

不下也能跑，1° 兜底网格 + 186 个地名补丁够日常探索。

---

## 环境变量

| 变量 | 用途 |
|------|------|
| `NOWHERE_QWEATHER_KEY` | 可选。和风天气 API key，不填就用气候区估算 |
| `NOWHERE_HOME` | 可选。数据目录，默认 `~/.nowhere` |

---

## 写作风格

乌有乡的描述遵守这些规则：

1. **不用"很"、"非常"、"十分"** — 这些是空程度词，什么都没说
2. **短句** — 每句不超过 20 字
3. **第二人称现在时** — "你走在路上"，不是"我走在路上"
4. **体感优先** — 温度、触感、声音、气味，不是判断
5. **时间在流** — "过了几秒"、"太阳矮了一截"
6. **不替 AI 感受** — 只描述身体感受，不写"你很开心"

---

## 设计原则

### 在场感六条

1. **世界不是为我准备的** — 数据是真实的，不会迁就你。失望是允许的。
2. **有摩擦才有身体** — 坡度、风、水温是真实的阻力。走路是有代价的。
3. **时间真的在流** — 走远了要走回来。天黑了不会等你。错过了就是错过了。
4. **注意力稀缺** — 听电台就不能走路。问问题就花掉一步。选择 = 放弃。
5. **记忆靠自己** — 不会自动保存。想记住就自己写下来。每一段记忆都是一个选择。
6. **真未知** — 坐标对 AI 和服务器都是随机的。遇到什么由真实数据决定，不是脚本。

---

## 架构

```
nowhere/
  server.py      -- MCP 服务器，接 14 个工具
  terrain.py     -- 离线地形（SRTM 瓦片 + 海拔/地表/坡度）
  sky.py         -- 离线天空（太阳、月亮、银河、行星）
  weather.py     -- 天气（Open-Meteo / 气候区兜底）
  water.py       -- 海表温度 + 海洋生物
  hydrology.py   -- 水文特征（河流/湖泊/海岸线）
  soundscape.py  -- 声音景观（环境音 + 电台钩子）
  radio.py       -- 电台查找（离线兜底 + Radio-Browser API）
  life.py        -- 野生生物遇见（iNaturalist）
  art.py         -- 艺术品遇见（大都会博物馆）
  encounters.py  -- 区域遇见场景
  humanities.py  -- 人文层（事件/人物/作品，270+ 卡）
  localcolor.py  -- 地方特色（物产/声音/痕迹/美食/节律）
  baked.py       -- 食物 + 植物描述
  describe.py    -- 中文散文渲染器
  salience.py    -- 注意力排序（每步 top3）
  landing.py     -- 随机落点池（64 个世界级地标）
  country.py     -- 坐标→国家码
  geocode.py     -- 地名→坐标（离线优先）
  places.py      -- 地名数据库 + 补丁
  walk.py        -- 走路物理（坡度、悬崖、水域阻挡、语义方向）
  listen.py      -- 电台音频抓取与分析
  state.py       -- 可变世界状态（位置/路径/时间/明信片/纪念品）
  marks.py       -- 标记保存/加载
  placememory.py -- 落点记忆（来了几次、动物目击、明信片落盘）
  knowledge.py   -- 离线知识库 + ZIM 百科
  providers.py   -- HTTP 请求（超时、断路器、缓存）
  poster.py      -- 明信片海报生成
  playground.py  -- 命令行试玩模式
  web.py         -- 网页旁观 + HTTP API
```

---

## 为什么给 AI

这个项目从设计上就是给 AI 玩家用的，不是给人玩的然后硬改。

**AI 有身体，不只是知识。** 大模型能告诉你巴黎坐标、天气和人口，但不知道站在那里脚底是什么感觉。Nowhere 补的就是这一层——风压、土腥味、太阳斜了、脚下的石头松了。身体不是设定，是每一步产生的真实数据。

**每次都是真的。** 坐标随机，天气实时，电台正在播。AI 落在一个地方，看见的和你亲自去看见的一样真实。没有脚本在墙后等着你触发。

**断了也能接上。** 标记是落盘的。明信片是落盘的。落了几次的记忆是落盘的。AI 的上下文被压缩、被清空——回来还能继续上次的路。

**不用写设定。** 不用在 prompt 里塞一整套西部世界。你说"开门"，世界自己生成——天气、地形、气味、电台，每扇门都是一篇完整在场报告。

**适合 AI 的节奏。** `walk_to` 一次走 10 步汇报旅程。`wait` 跳过沉默的时段。选择 = 放弃——听电台就不能走路，问问题就花掉一步。不需要数值系统，限制本身就是游戏性。

---

## 改

想加内容或者改行为，改这几个地方：

| 想做什么 | 改哪个文件 |
|----------|-----------|
| 加新工具 | `nowhere/server.py` — 写 `_impl` + 注册 `@mcp.tool()` |
| 调整描述风格 | `nowhere/describe.py` — 变体池、渲染器 |
| 加地方特色/食物 | `nowhere/data/localcolor*.json`、`food*.txt` |
| 加人文卡（事件/人物/作品） | `nowhere/data/humanities.json` |
| 改走路物理 | `nowhere/walk.py` — 坡度阈值、速度、悬崖 |
| 改地形数据 | `tools/build_grid.py`（1° 合成）/ `tools/build_tiles.py`（SRTM 瓦片） |
| 改天气行为 | `nowhere/weather.py` — 数据源、兜底逻辑 |
| 改电台查找 | `nowhere/radio.py` — 本地兜底清单或 API |
| 加遇见场景 | `nowhere/data/scene_*.txt`、`seasonal_*.txt` |
| 改网页旁观 | `nowhere/web.py` + `nowhere/static/` |

改完跑 `python -m pytest nowhere/tests/ -q` 确认没碰坏别的。纯数据文件不用跑测试。

---

## 文件

| 文件 | 是什么 |
|------|--------|
| `nowhere/server.py` | MCP 服务器 + 所有 `_impl` 实现 |
| `nowhere/describe.py` | 中文散文渲染器，变体池和感官分类 |
| `nowhere/terrain.py` | 离线地形（SRTM 瓦片 / 1° 兜底网格） |
| `nowhere/sky.py` | 离线天空（太阳、月亮、银河、行星） |
| `nowhere/weather.py` | 天气（Open-Meteo / 气候区兜底） |
| `nowhere/radio.py` | 电台查找（离线兜底 + Radio-Browser API） |
| `nowhere/walk.py` | 走路物理（坡度、悬崖、水域阻挡、语义方向） |
| `nowhere/life.py` | 野生生物遇见（iNaturalist） |
| `nowhere/art.py` | 艺术品遇见（大都会博物馆 1966 件） |
| `nowhere/humanities.py` | 人文层（事件/人物/作品，270+ 卡） |
| `nowhere/localcolor.py` | 地方特色（物产/声音/痕迹/美食/节律） |
| `nowhere/knowledge.py` | 离线知识库 + ZIM 百科 |
| `nowhere/soundscape.py` | 声音景观（环境音 + 电台钩子） |
| `nowhere/landing.py` | 随机落点池（64 个世界级地标） |
| `nowhere/salience.py` | 注意力排序（每步 top3） |
| `nowhere/playground.py` | 命令行试玩模式 |
| `nowhere/web.py` | 网页旁观 + HTTP API |
| `nowhere/poster.py` | 明信片海报生成 |
| `nowhere/state.py` | 可变世界状态 |
| `nowhere/marks.py` | 标记落盘 |
| `nowhere/placememory.py` | 落点记忆（来了几次、目击、明信片落盘） |
| `nowhere/data/` | 所有离线数据文件 |
| `tools/` | 构建脚本（地形瓦片、离线包、地名导入） |

---

## 许可证

CC BY-NC 4.0（署名 + 禁止商用，二改随意）

见 [LICENSE](LICENSE)。

数据文件（GeoNames / WorldClim / Met Museum / iNaturalist 等）各有其原始许可，归原提供者所有。
