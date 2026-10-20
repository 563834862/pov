"""采集来源总目录：分层登记、状态校验、看板/报告展示。

依据《舆情与AI新闻采集来源总清单》（2026-10 调研版）建立。

## 分层（L0 → L3，优先级由高到低）

清单第十一章「优先级原则」要求：**官方 API > 原生 RSS > RSSHub > 爬虫**。
本目录据此给每个源标注 tier，新增源必须先归层再接入：

    L0 api      官方/站点公开 JSON 接口，免 key（最稳，优先接）
    L1 rss      站点原生 RSS / Atom（管道稳定性最高的一层）
    L2 rsshub   需 Docker 自建 RSSHub 的路由（依赖外部基建，默认不启用）
    L3 scrape   页面解析（结构易变，仅在前三层无覆盖时兜底）
    bridge      Agent / 浏览器桥接喂入（被墙或强风控平台）

## 状态

    active        已接入且本机实测可达
    needs-rsshub  依赖自建 RSSHub，未自建前不启用
    deprecated    已关停/退役，禁止接入（清单第十章避坑清单）
"""
from .ai_sources import AI_SOURCE_LABELS, AI_SOURCES
from .hotlist import HOTLIST_SOURCE_LABELS, HOTLIST_SOURCES
from .media_sources import MEDIA_SOURCE_LABELS, MEDIA_SOURCES
from .overseas_collector import OVERSEA_SOURCE_LABELS, OVERSEA_SOURCES
from .policy_sources import POLICY_SOURCE_LABELS, POLICY_SOURCES

TIER_LABELS = {
    "api": "L0 官方接口",
    "rss": "L1 原生RSS",
    "rsshub": "L2 RSSHub自建",
    "scrape": "L3 页面解析",
    "bridge": "桥接喂入",
}

GROUP_LABELS = {
    "domestic": "国内舆情",
    "overseas": "海外舆情",
    "policy": "政策层",
    "ai": "AI一手信源",
    "media": "媒体/社区",
    "hotlist": "热度榜",
}

# ---------------------------------------------------------------- 源目录
# id: (label, tier, group, status, note)
SOURCE_CATALOG = {
    # ---- 国内舆情 ----
    "search_engine": ("搜索引擎", "bridge", "domestic", "active",
                      "由 Agent/用户放入 data/inputs/search-*.json"),
    "kr36": ("36氪", "scrape", "domestic", "active", "搜索页解析，站点为 SPA 需容错"),
    "baidu_news": ("百度新闻", "scrape", "domestic", "active", "新闻搜索页解析"),
    "toutiao": ("今日头条", "scrape", "domestic", "active", "搜索页解析，标题含 <em> 高亮"),
    "bilibili_rank": ("B站热榜", "api", "domestic", "active", "全站排行 JSON，按标题过滤 AI 相关"),
    # ---- 热度榜（清单第七章）----
    "weibo_hot": ("微博热搜", "api", "hotlist", "active",
                  "★ 免费热度榜，需带 Referer 否则 403"),
    "baidu_hot": ("百度热搜", "api", "hotlist", "active", "★ 免费热度榜，PC board 接口"),
    "toutiao_hot": ("头条热榜", "api", "hotlist", "active", "★ 免费热度榜，含 HotValue"),
    # ---- 政策层（清单第一、二章）----
    "gov_policy": ("中国政府网政策", "api", "policy", "active",
                   "★ 国务院政策文件库检索接口，无官方 API 故容错解析"),
    "federal_register": ("美联邦公报", "api", "policy", "active",
                         "★ 官方 API 完全免费无 key，含规则/行政令/关税"),
    "gdelt": ("GDELT全球事件", "api", "policy", "active",
              "★ 免 key 全球事件库，15 分钟更新，自带 tone 情绪分"),
    # ---- AI 一手信源（清单第四章）----
    "arxiv": ("arXiv论文", "api", "ai", "active",
              "★ 官方 API，cs.AI/CL/LG/CV/MA，限 1 请求/3 秒"),
    "hf_papers": ("HF每日论文", "api", "ai", "active",
                  "★ /api/daily_papers 免认证，含 upvotes 互动量"),
    "github_trending": ("GitHub趋势", "scrape", "ai", "active",
                        "★ 开源动态风向标，Search API 兜底"),
    # ---- 媒体/社区（清单第五章）----
    "hackernews": ("HackerNews", "api", "media", "active",
                   "★ 官方 Firebase + Algolia API，无 key 无限流"),
    "producthunt": ("ProductHunt", "rss", "media", "active", "每日新品 Atom feed"),
    "techcrunch_ai": ("TechCrunch", "rss", "media", "active", "AI 专栏原生 RSS"),
    "theverge_ai": ("TheVerge", "rss", "media", "active", "AI 专栏原生 RSS"),
    "arstechnica": ("ArsTechnica", "rss", "media", "active", "主站 RSS"),
    "mittr": ("MIT科技评论", "rss", "media", "active", "主站 RSS"),
    "openai_news": ("OpenAI官方", "rss", "media", "active", "官方新闻 RSS，剔除客户案例稿"),
    "venturebeat_ai": ("VentureBeat", "rss", "media", "active", "AI 专栏原生 RSS"),
    "qbitai": ("量子位", "rss", "media", "active", "★ 中文原生 RSS，稳定性最高一层"),
    "infoq": ("InfoQ", "rss", "media", "active", "★ 中文原生 RSS"),
    # ---- 桥接喂入（被墙/强风控）----
    "x_search": ("X平台", "bridge", "overseas", "active",
                 "域名被墙，由 Agent WebSearch 喂入 data/inputs/oversea-x-*.json"),
    "youtube_search": ("YouTube", "bridge", "overseas", "active",
                       "域名被墙，由 Agent WebSearch 喂入 data/inputs/oversea-yt-*.json"),
    # ---- 需自建 RSSHub（默认不启用）----
    "jiqizhixin": ("机器之心", "rsshub", "media", "needs-rsshub",
                   "原生 RSS 已失效（跳转 /data-service），需 RSSHub 自建路由"),
    "xinzhiyuan": ("新智元", "rsshub", "media", "needs-rsshub", "需 RSSHub 自建路由"),
    "cls_telegraph": ("财联社电报", "rsshub", "policy", "needs-rsshub",
                      "nodeapi 直连已 404，需 RSSHub /cls/telegraph 路由"),
    "mofcom": ("商务部公告", "rsshub", "policy", "needs-rsshub", "公告栏需 RSSHub 路由"),
    "miit": ("工信部通告", "rsshub", "policy", "needs-rsshub",
             "RSS 接口已 404，需 RSSHub 路由或 changedetection"),
    "cac": ("国家网信办", "rsshub", "policy", "needs-rsshub",
            "rss.xml 已 404，生成式AI备案/算法备案高优先，建议 changedetection 监控"),
}

# ---------------------------------------------------------------- 来源类别
# 用于把事件区分为「媒体热度」与「学术/开源前沿信号」：
#   论文与开源项目是**超前信号**，进入事件库后多表现为独苗/偏早，
#   正是判断博主「提前卡位」的判据，不应因为热度低而与媒体事件混为一谈。
SOURCE_CLASS = {
    "arxiv": "学术信号",
    "hf_papers": "学术信号",
    "github": "开源动态",
    "gov_policy": "政策层",
    "federal_register": "政策层",
    "gdelt": "全球事件",
    "weibo_hot": "热度榜",
    "baidu_hot": "热度榜",
    "toutiao_hot": "热度榜",
    "hackernews": "开发者社区",
    "producthunt": "新品发布",
    "x_search": "社交平台",
    "youtube_search": "视频平台",
    "bilibili_rank": "视频平台",
}

DEFAULT_CLASS = "媒体报道"

# 「前沿信号层」：论文 / 开源动态。
# 这些条目是**超前信号**而非已成形舆情——它们几乎必定是独苗事件，
# 若参与验证判据，会让博主因为「没追一篇没人讨论的论文」而被误判滞后。
# 因此：验证匹配时跳过，仅在报告/看板中作为「前沿信号」单独呈现，
# 供判断博主是否具备提前卡位能力。
SIGNAL_CLASSES = {"学术信号", "开源动态"}


def class_of(platform: str) -> str:
    """平台 → 来源类别（未知平台归入媒体报道）。"""
    return SOURCE_CLASS.get(platform or "", DEFAULT_CLASS)


# ---------------------------------------------------------------- 已关停源（禁止接入）
# 引自清单第十章「2026 避坑清单（重大变化时间线）」
DEPRECATED_SOURCES = {
    "bilibili-API-collect": ("2026-01-28", "被 B站法务函件要求永久关停",
                             "B站改用 Nemo2011/bilibili-api（WBI 签名）"),
    "tiny-tiny-rss": ("2025-11-01", "原项目已关停", "迁移 FreshRSS"),
    "nitter": ("2026-08", "收 X 律师函，仓库归档", "改用 twscrape/Scweet 或付费 X API"),
    "snscrape": ("2026-08", "对 X 已报废", "改用 twscrape/Scweet 或付费 X API"),
    "twint": ("2026-08", "对 X 已报废", "改用 twscrape/Scweet 或付费 X API"),
    "bing-news-api": ("2025-08", "全面退役", "改用 GDELT / Google News RSS"),
    "piped": ("2025-08 起", "实例被大规模封杀", "改用 Invidious 自建"),
    "newsnow": ("2026", "代码冻结", "继任 NewsNext 开发中，暂不依赖"),
    "spider-flow": ("2026", "原仓库停滞 4 年+", "改用 EasySpider / MediaCrawler"),
    "crawlab": ("2026", "更新放缓", "选用前核对最近提交"),
    "reddit-public-api": ("2027-03 预定", "公共 API 终止", "尽早申请 OAuth；历史数据用 Arctic Shift"),
}


def is_known(sid: str) -> bool:
    return sid in SOURCE_CATALOG or sid in DEPRECATED_SOURCES


def meta(sid: str) -> dict:
    """取源元数据；未知源返回兜底结构（不抛异常）。"""
    if sid in SOURCE_CATALOG:
        label, tier, group, status, note = SOURCE_CATALOG[sid]
    elif sid in DEPRECATED_SOURCES:
        when, what, fix = DEPRECATED_SOURCES[sid]
        label, tier, group = sid, "deprecated", "deprecated"
        status, note = "deprecated", f"{when} {what}；{fix}"
    else:
        label, tier, group = sid, "unknown", "unknown"
        status, note = "unknown", "未登记来源"
    return {"id": sid, "label": label, "tier": tier, "group": group,
            "tier_label": TIER_LABELS.get(tier, tier),
            "group_label": GROUP_LABELS.get(group, group),
            "status": status, "note": note}


def validate(sources: list) -> list:
    """校验配置里的源清单，返回告警文本列表（空=无问题）。

    两类问题必须挡住：
      1) 未知源 —— 拼写错误会导致源静默失效（采集不到却无报错）；
      2) 已关停源 —— 清单第十章明确禁止，接了也拿不到数据。
    """
    warns = []
    for sid in sources or []:
        if sid in DEPRECATED_SOURCES:
            when, what, fix = DEPRECATED_SOURCES[sid]
            warns.append(f"[{sid}] 已关停（{when} {what}）——{fix}")
        elif sid not in SOURCE_CATALOG:
            warns.append(f"[{sid}] 未登记来源，请检查拼写或先在 source_registry 登记")
        elif SOURCE_CATALOG[sid][3] == "needs-rsshub":
            warns.append(f"[{sid}] 依赖自建 RSSHub，未自建前拿不到数据")
    return warns


def summary(cfg: dict) -> dict:
    """按分层汇总当前启用的源，供看板/报告展示。"""
    bcfg = cfg.get("baseline", {})
    extra = cfg.get("sources_extra", {})
    sources = list(bcfg.get("sources") or []) + list(extra.get("enabled") or [])
    by_tier, by_group = {}, {}
    for sid in sources:
        m = meta(sid)
        by_tier.setdefault(m["tier"], []).append(m)
        by_group.setdefault(m["group"], []).append(m)
    return {
        "total": len(sources),
        "by_tier": {k: by_tier[k] for k in sorted(by_tier)},
        "by_group": {k: by_group[k] for k in sorted(by_group)},
        "warnings": validate(sources),
    }


# ---------------------------------------------------------------- 采集器注册表
def build_collectors(domestic: dict = None) -> dict:
    """合并全部采集器：国内（baseline 自带）+ 海外 + AI + 政策 + 热榜。"""
    cols = {}
    if domestic:
        cols.update(domestic)
    cols.update(OVERSEA_SOURCES)
    cols.update(AI_SOURCES)
    cols.update(POLICY_SOURCES)
    cols.update(HOTLIST_SOURCES)
    cols.update(MEDIA_SOURCES)
    return cols


SOURCE_LABELS = {}
SOURCE_LABELS.update(OVERSEA_SOURCE_LABELS)
SOURCE_LABELS.update(AI_SOURCE_LABELS)
SOURCE_LABELS.update(POLICY_SOURCE_LABELS)
SOURCE_LABELS.update(HOTLIST_SOURCE_LABELS)
SOURCE_LABELS.update(MEDIA_SOURCE_LABELS)
SOURCE_LABELS.update({
    "search_engine": "搜索引擎", "kr36": "36氪", "baidu_news": "百度新闻",
    "toutiao": "今日头条", "bilibili_rank": "B站热榜",
})
