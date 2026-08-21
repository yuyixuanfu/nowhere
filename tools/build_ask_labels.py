"""卡88: 用embedding聚类给ask_kb打标签。

用SiliconFlow API (BAAI/bge-large-zh-v1.5, 1024维) 对ask_kb的key做embedding，
然后KMeans聚类，人工命名标签，输出ask_labels.json。

依赖: D:/Users/chat/embedding.py (SiliconFlow API封装)
"""
import json, sys, os, math
sys.path.insert(0, "D:/Users/chat")
from embedding import embed_batch, get_dim

KB_PATH = "nowhere/data/ask_kb.json"
LABELS_PATH = "nowhere/data/ask_labels.json"
BATCH = 32

# 读取ask_kb的key
with open(KB_PATH, encoding="utf-8") as f:
    kb = json.load(f)
keys = list(kb.keys())
print(f"Total keys: {len(keys)}")

# 批量生成embedding
print(f"Generating embeddings (dim={get_dim()})...")
all_vecs = []
for i in range(0, len(keys), BATCH):
    chunk = keys[i:i+BATCH]
    vecs = embed_batch(chunk)
    all_vecs.extend(vecs)
    print(f"  {i+len(chunk)}/{len(keys)}", flush=True)
print(f"Embeddings done: {len(all_vecs)}")

# KMeans聚类
from sklearn.cluster import KMeans
import numpy as np

X = np.array(all_vecs)
# 先用30个簇，后面人工命名
N_CLUSTERS = 30
print(f"KMeans k={N_CLUSTERS}...")
km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=10)
labels = km.fit_predict(X)

# 统计每个簇的样本
clusters = {}
for idx, label in enumerate(labels):
    clusters.setdefault(label, []).append(keys[idx])

# 打印每个簇的前10个样本，供人工命名
print("\n=== 聚类结果 ===")
for c in range(N_CLUSTERS):
    members = clusters[c]
    print(f"\n簇{c} ({len(members)}条): {', '.join(members[:10])}")

# 自动打标签：用关键词规则给每个簇命名
def auto_tag(members):
    """根据簇内成员的关键词，推断标签。"""
    tags = set()
    text = "".join(members)
    # 地标
    landmark_kw = ["寺", "庙", "宫", "塔", "殿", "楼", "阁", "桥", "门", "园", "陵",
                   "窟", "广场", "城堡", "教堂", "宫殿", "遗址", "纪念碑", "铁塔",
                   "歌剧院", "博物馆", "美术馆", "公园", "花园", "城墙", "古镇", "古城"]
    if sum(1 for m in members if any(k in m for k in landmark_kw)) > len(members) * 0.3:
        tags.add("地标")
    # 地名
    place_kw = ["城", "市", "镇", "村", "区", "县", "省", "州", "国", "邦", "都", "京"]
    if sum(1 for m in members if any(k in m for k in place_kw)) > len(members) * 0.3:
        tags.add("地名")
    # 饮食
    food_kw = ["菜", "饭", "面", "汤", "肉", "鱼", "虾", "酒", "茶", "咖啡", "饼",
               "糕", "果", "瓜", "豆", "米", "麦", "粉", "酱", "火锅", "烤", "炸",
               "蒸", "煮", "炒", "炖", "焖", "煎", "拌", "烧", "卤", "腌", "熏"]
    if sum(1 for m in members if any(k in m for k in food_kw)) > len(members) * 0.3:
        tags.add("饮食")
    # 历史政体
    hist_kw = ["朝", "帝国", "王朝", "王国", "共和国", "联邦", "汗国", "苏丹国", "公国"]
    if sum(1 for m in members if any(k in m for k in hist_kw)) > len(members) * 0.3:
        tags.add("历史政体")
    # 节日
    fest_kw = ["节", "祭", "庆典", "狂欢", "新年", "圣诞", "复活", "感恩", "万圣",
               "清明", "端午", "中秋", "重阳", "除夕", "元宵"]
    if sum(1 for m in members if any(k in m for k in fest_kw)) > len(members) * 0.3:
        tags.add("节日")
    # 自然
    nature_kw = ["山", "河", "海", "洋", "湖", "沙漠", "森林", "草原", "雨林", "高原",
                 "盆地", "峡谷", "瀑布", "温泉", "冰川", "火山", "岛", "半岛", "海峡"]
    if sum(1 for m in members if any(k in m for k in nature_kw)) > len(members) * 0.3:
        tags.add("自然")
    # 动物
    animal_kw = ["虎", "狮", "象", "熊", "猴", "鹿", "马", "牛", "羊", "猪", "狗",
                 "猫", "鸟", "鹰", "鹤", "蛇", "龙", "鱼", "鲸", "鲨", "龟", "蛙",
                 "蝶", "蜂", "蚁", "企鹅", "袋鼠", "考拉", "熊猫", "猩猩", "鳄"]
    if sum(1 for m in members if any(k in m for k in animal_kw)) > len(members) * 0.3:
        tags.add("动物")
    # 音乐
    music_kw = ["乐", "曲", "歌", "唱", "奏", "琴", "笛", "鼓", "号", "弦", "管",
                "交响", "协奏", "奏鸣", "夜曲", "圆舞", "进行曲", "摇滚", "爵士",
                "蓝调", "嘻哈", "电子", "民谣", "乡村", "雷鬼", "萨尔萨"]
    if sum(1 for m in members if any(k in m for k in music_kw)) > len(members) * 0.3:
        tags.add("音乐")
    # 体育
    sport_kw = ["球", "赛", "跑", "跳", "游", "滑", "骑", "射", "拳", "击", "摔",
                "举", "操", "剑", "马术", "帆", "冲", "潜", "攀", "蹦", "跳伞"]
    if sum(1 for m in members if any(k in m for k in sport_kw)) > len(members) * 0.3:
        tags.add("体育")
    # 学科
    subject_kw = ["学", "科", "论", "理", "法", "术", "技", "工程", "研究", "分析",
                  "物理", "化学", "生物", "数学", "天文", "地质", "地理", "气象"]
    if sum(1 for m in members if any(k in m for k in subject_kw)) > len(members) * 0.3:
        tags.add("学科")
    # 职业
    prof_kw = ["师", "家", "员", "长", "官", "使", "医", "教师", "教授", "律师",
               "法官", "警察", "消防", "军人", "飞行员", "船长", "厨师", "农民"]
    if sum(1 for m in members if any(k in m for k in prof_kw)) > len(members) * 0.3:
        tags.add("职业")
    # 国家
    country_kw = ["中国", "日本", "韩国", "印度", "泰国", "越南", "法国", "德国",
                  "英国", "意大利", "西班牙", "俄罗斯", "美国", "加拿大", "巴西",
                  "澳大利亚", "埃及", "南非", "土耳其", "伊朗", "希腊", "波兰"]
    if sum(1 for m in members if any(k in m for k in country_kw)) > len(members) * 0.3:
        tags.add("国家")
    # 语言
    lang_kw = ["语", "话", "文"]
    if sum(1 for m in members if any(k in m for k in lang_kw)) > len(members) * 0.3:
        tags.add("语言")
    # 建筑
    arch_kw = ["建筑", "式", "风格", "主义"]
    if sum(1 for m in members if any(k in m for k in arch_kw)) > len(members) * 0.3:
        tags.add("建筑")
    # 服装
    cloth_kw = ["衣", "裙", "裤", "鞋", "帽", "袜", "手套", "围巾", "领带", "外套",
                "夹克", "大衣", "毛衣", "T恤", "衬衫", "制服", "婚纱", "旗袍", "汉服"]
    if sum(1 for m in members if any(k in m for k in cloth_kw)) > len(members) * 0.3:
        tags.add("服装")
    # 科技
    tech_kw = ["计算", "网络", "通信", "技术", "芯片", "软件", "硬件", "算法", "数据",
               "人工智能", "机器人", "无人机", "打印", "现实", "能源", "量子", "区块"]
    if sum(1 for m in members if any(k in m for k in tech_kw)) > len(members) * 0.3:
        tags.add("科技")
    # 医学
    med_kw = ["医", "药", "病", "疗", "诊", "手术", "疫苗", "基因", "细胞", "器官",
              "针灸", "推拿", "拔罐", "刮痧", "太极", "气功", "瑜伽"]
    if sum(1 for m in members if any(k in m for k in med_kw)) > len(members) * 0.3:
        tags.add("医学")
    # 艺术
    art_kw = ["画", "雕", "塑", "绘", "写", "摄", "导", "演", "剧", "影", "美", "艺"]
    if sum(1 for m in members if any(k in m for k in art_kw)) > len(members) * 0.3:
        tags.add("艺术")
    # 人物
    person_kw = ["先生", "女士", "皇帝", "国王", "王后", "将军", "大臣", "圣", "师",
                 "祖", "帝", "后", "妃", "公", "侯", "伯", "子", "男", "总统", "总理"]
    if sum(1 for m in members if any(k in m for k in person_kw)) > len(members) * 0.3:
        tags.add("人物")
    # 事件
    event_kw = ["革命", "战争", "改革", "运动", "起义", "独立", "统一", "条约", "会议"]
    if sum(1 for m in members if any(k in m for k in event_kw)) > len(members) * 0.3:
        tags.add("事件")
    # 风俗
    custom_kw = ["风俗", "习俗", "传统", "仪式", "典礼", "祭祀", "婚丧", "礼仪"]
    if sum(1 for m in members if any(k in m for k in custom_kw)) > len(members) * 0.3:
        tags.add("风俗")
    # 家具
    furn_kw = ["床", "沙发", "椅", "桌", "柜", "灯", "镜", "毯", "帘", "枕", "被"]
    if sum(1 for m in members if any(k in m for k in furn_kw)) > len(members) * 0.3:
        tags.add("家具")
    # 工具
    tool_kw = ["锤", "螺丝", "扳手", "钳", "锯", "斧", "铲", "锄", "镰", "剪",
               "刀", "钻", "砂纸", "刷", "胶", "绳", "锁", "钥匙", "钉", "螺"]
    if sum(1 for m in members if any(k in m for k in tool_kw)) > len(members) * 0.3:
        tags.add("工具")
    # 交通
    trans_kw = ["车", "船", "飞机", "火车", "地铁", "公交", "出租", "自行车", "摩托",
                "火箭", "航天", "潜水", "缆车", "索道", "热气球", "飞艇"]
    if sum(1 for m in members if any(k in m for k in trans_kw)) > len(members) * 0.3:
        tags.add("交通")
    # 矿物
    mineral_kw = ["钻石", "红宝石", "蓝宝石", "翡翠", "玉石", "玛瑙", "水晶", "琥珀",
                  "珍珠", "珊瑚", "矿", "石", "岩", "晶"]
    if sum(1 for m in members if any(k in m for k in mineral_kw)) > len(members) * 0.3:
        tags.add("矿物")
    # 杂物
    if not tags:
        tags.add("杂物")
    return list(tags)

# 为每个key打标签
labels_map = {}
for c in range(N_CLUSTERS):
    members = clusters[c]
    tags = auto_tag(members)
    for m in members:
        labels_map[m] = tags

# 保存
with open(LABELS_PATH, "w", encoding="utf-8") as f:
    json.dump(labels_map, f, ensure_ascii=False, indent=2)
print(f"\nSaved {len(labels_map)} labels to {LABELS_PATH}")

# 统计标签分布
from collections import Counter
tag_counts = Counter()
for tags in labels_map.values():
    for t in tags:
        tag_counts[t] += 1
print("\n标签分布:")
for tag, count in tag_counts.most_common():
    print(f"  {tag}: {count}")
