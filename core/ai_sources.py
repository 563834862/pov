"""AI 一手信源采集器：arXiv / HuggingFace Daily Papers / GitHub Trending / VentureBeat AI。

定位（引自《舆情与AI新闻采集来源总清单》第四章）：
    技术突破往往先爆发于开源社区和核心人物社媒，而非新闻稿——
    **这一层是 AI 舆情监控的信噪比高地**。

四个源全部为免 key 公开接口，符合清单第十一章「官方 API > 原生 RSS >
RSSHub > 爬虫」的优先级原则：

    arXiv        ★ 官方 API（Atom XML，1 请求/3 秒）
    HF Papers    ★ 官方 API（/api/daily_papers，免认证读）
    GitHub       ★ 官方 Search API 为主、Trending 页解析兜底
    VentureBeat  原生 RSS（英文媒体 AI 专栏）

与「新闻源」的差异：论文与开源项目是**超前信号**——它们进入事件库后多表现为
「独苗/偏早」，正是博主「提前卡位」的判据来源，不应因为热度低而被过滤。
"""
import re
from datetime import datetime, timedelta, timezone

from .feedkit import _is_promo, _mk_item, clean_text, http_get, http_get_json
from .overseas_collector import _coerce_dt, _is_ai_any, _iso, _parse_feed

# arXiv 分类：AI 全领域主战场
ARXIV_CATS = ["cs.AI", "cs.CL", "cs.LG", "cs.CV", "cs.MA"]

# 默认英文检索词（可在 config.yaml 的 sources_extra.arxiv_queries 覆盖）
# 注意：仅用于**本地**相关性过滤，不发送给 arXiv（关键词检索会触发限流）
DEFAULT_ARXIV_QUERIES = ["AI video generation", "AI agent", "open weights model"]

# 学术/开源类条目打标：事件库据此区分「媒体热度」与「前沿信号」
PAPER_TAG = "学术信号"
OSS_TAG = "开源动态"

_ARTICLE_RE = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
_REPO_RE = re.compile(r'<h2 class="h3 lh-condensed">\s*<a[^>]*href="/([^"]+)"', re.S)
_DESC_RE = re.compile(r'<p class="col-9 color-fg-muted my-1 pr-4">\s*(.*?)\s*</p>', re.S)
_TODAY_RE = re.compile(r"([\d,]+)\s+stars today")
_LANG_RE = re.compile(r'<span itemprop="programmingLanguage">([^<]+)</span>')


def _limit(s: str, n: int) -> str:
    return (s or "")[:n]


# ---------------------------------------------------------------- arXiv
def collect_arxiv(keywords: list, top_n: int, queries: list = None) -> tuple:
    """arXiv 官方 API：按 AI 分类取最新提交，本地做相关性过滤。

    为什么不用关键词检索（``all:"..."``）：
      实测 export.arxiv.org 对关键词查询普遍返回 429 或 30s+ 超时——
      关键词检索需扫全库，开销远大于「按分类 + 提交时间排序取最新」；
      而 ``cat:cs.AI`` 类查询稳定 200、约 1.4s 返回。
      故改为**按分类取最新 + 本地相关性过滤**，既稳又准。

    相关性为什么用词典命中而非 "AI" 泛匹配：
      cs.CV 里大量纯视觉理论论文与博主选题无关；只有命中领域词典
      （提到 Sora / ComfyUI / FLUX，或涉及文生视频 / 智能体 / 大模型…）
      才构成有效舆情信号，避免论文流稀释事件库。
    """
    cat_q = "+OR+".join(f"cat:{c}" for c in ARXIV_CATS)
    url = ("https://export.arxiv.org/api/query?search_query=" + cat_q +
           f"&start=0&max_results={max(20, top_n * 3)}"
           "&sortBy=submittedDate&sortOrder=descending")
    try:
        entries = _parse_feed(http_get(url, timeout=30, retries=1, backoff=3.0))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    qs = [str(q).lower() for q in (queries or DEFAULT_ARXIV_QUERIES)]
    seen, items = set(), []
    for e in entries:
        title = clean_text(e["title"])          # 标题常含换行，需折叠
        summary = clean_text(e["summary"])
        if not title or title in seen:
            continue
        blob = (title + " " + summary).lower()
        if not (_topic_hit(title + " " + summary) or any(_fuzzy_hit(q, blob) for q in qs)):
            continue
        seen.add(title)
        aid = ""
        m = re.search(r"abs/([0-9v.]+)", e["url"] or "")
        if m:
            aid = m.group(1)
        it = _mk_item(
            platform="arxiv",
            title=title,
            url=e["url"] or (f"https://arxiv.org/abs/{aid}" if aid else ""),
            author=e["author"],
            published=e["published"],
            text=summary[:300],
        )
        it["tags"].append(PAPER_TAG)
        items.append(it)
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


def _topic_hit(text: str) -> bool:
    """命中领域标准话题（复用词典，含词边界保护）。"""
    from .lexicon import extract_topics
    return bool(extract_topics(text or ""))


def _fuzzy_hit(query: str, blob: str) -> bool:
    """多词检索词模糊命中：≥60% 词元出现即算命中。"""
    toks = re.findall(r"[a-z0-9]+", query or "")
    if not toks:
        return False
    hit = sum(1 for t in toks if t in blob)
    return hit >= max(1, int(len(toks) * 0.6 + 0.5))


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


# ---------------------------------------------------------------- HuggingFace Daily Papers
def collect_hf_papers(keywords: list, top_n: int) -> tuple:
    """HuggingFace 每日精选论文（编辑推荐，含点赞数/讨论数/githubStars）。

    相比 arXiv 原始流，这一层已做人工筛选，信噪比更高；
    upvotes 直接作为互动量参与热度计算。
    """
    try:
        data = http_get_json("https://huggingface.co/api/daily_papers", timeout=25)
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    items = []
    for row in data or []:
        p = row.get("paper") or {}
        title = clean_text(p.get("title") or row.get("title") or "")
        summary = clean_text(p.get("summary") or row.get("summary") or "")
        if not title or not _topic_hit(title + " " + summary):
            continue
        pid = p.get("id") or ""
        up = int(p.get("upvotes") or 0)
        cm = int(row.get("numComments") or 0)
        stars = int(p.get("githubStars") or 0)
        it = _mk_item(
            platform="hf_papers",
            title=title,
            url=f"https://huggingface.co/papers/{pid}" if pid else "https://huggingface.co/papers",
            author=((row.get("submittedBy") or {}) or {}).get("name", "")
            if isinstance(row.get("submittedBy"), dict) else "",
            published=_iso(_coerce_dt(p.get("publishedAt") or row.get("publishedAt") or "")),
            text=summary[:300],
            metrics={"views": stars, "likes": up, "comments": cm, "reposts": 0,
                     "engagement_total": up + cm, "rank": None},
        )
        it["tags"].append(PAPER_TAG)
        items.append(it)
    items.sort(key=lambda x: -int((x.get("metrics") or {}).get("engagement_total") or 0))
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


# ---------------------------------------------------------------- GitHub Trending
def _parse_trending(html: str) -> list:
    """解析 GitHub Trending 页面：仓库全名 / 描述 / 今日星标 / 语言。"""
    out = []
    for blk in _ARTICLE_RE.findall(html or ""):
        m = _REPO_RE.search(blk)
        if not m:
            continue
        full = m.group(1).strip("/")
        d = _DESC_RE.search(blk)
        t = _TODAY_RE.search(blk)
        g = _LANG_RE.search(blk)
        desc = clean_text(d.group(1)) if d else ""
        today = int((t.group(1) or "0").replace(",", "")) if t else 0
        out.append({
            "full_name": full,
            "description": desc,
            "stars_today": today,
            "language": (g.group(1) or "").strip() if g else "",
        })
    return out


def _gh_search_fallback(top_n: int) -> list:
    """GitHub Search API 兜底：近 7 天创建、星标 ≥50 的仓库，按星标排序。

    Trending 页面结构可能变更；Search API 是官方稳定接口，
    在未配置 token 时亦有较低但可用的匿名额度。
    """
    from datetime import date
    since = (date.today() - timedelta(days=7)).isoformat()
    url = ("https://api.github.com/search/repositories?q=created:>"
           f"{since}+stars:>=50&sort=stars&order=desc&per_page={min(30, max(10, top_n * 2))}")
    try:
        data = http_get_json(url, timeout=20,
                             headers={"Accept": "application/vnd.github+json"})
    except Exception:
        return []
    out = []
    for r in data.get("items") or []:
        out.append({
            "full_name": r.get("full_name") or "",
            "description": clean_text(r.get("description") or ""),
            "stars_today": int(r.get("stargazers_count") or 0),
            "language": r.get("language") or "",
        })
    return out


def collect_github_trending(keywords: list, top_n: int, token: str = "") -> tuple:
    """GitHub 趋势项目：Trending 页解析为主，Search API 兜底。

    只保留标题(仓库名)或描述命中 AI/科技关键词的条目，
    避免每日 Trending 里大量无关项目刷成假事件。
    """
    rows = []
    try:
        rows = _parse_trending(http_get("https://github.com/trending?since=daily", timeout=25))
    except Exception:
        rows = []
    if not rows:
        rows = _gh_search_fallback(top_n)
        if not rows:
            return [], "fail:unreachable"
    items = []
    for i, r in enumerate(rows, 1):
        full, desc = r["full_name"], r["description"]
        if not _is_ai_any(full + " " + desc):
            continue
        if _is_promo(full + " " + desc):
            continue
        today = int(r.get("stars_today") or 0)
        it = _mk_item(
            platform="github",
            title=full,
            url=f"https://github.com/{full}",
            author=(full.split("/")[0] if "/" in full else ""),
            published=datetime.now(timezone.utc).date().isoformat(),
            text=(f"{desc} · {r.get('language', '')} · {today} stars today").strip(" ·"),
            metrics={"views": 0, "likes": today, "comments": 0, "reposts": 0,
                     "engagement_total": today, "rank": i},
        )
        it["tags"].append(OSS_TAG)
        items.append(it)
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


# ---------------------------------------------------------------- VentureBeat AI
def collect_venturebeat(keywords: list, top_n: int) -> tuple:
    """VentureBeat AI 频道原生 RSS（英文媒体 AI 专栏）。"""
    try:
        entries = _parse_feed(http_get("https://venturebeat.com/category/ai/feed/", timeout=20))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    items = []
    for e in entries:
        title = clean_text(e["title"])
        if not title or not _is_ai_any(title + " " + e["summary"]):
            continue
        items.append(_mk_item(
            platform="venturebeat",
            title=title,
            url=e["url"],
            author=e["author"],
            published=e["published"],
            text=clean_text(e["summary"]),
        ))
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


# ---------------------------------------------------------------- 注册表
AI_SOURCES = {
    "arxiv": collect_arxiv,
    "hf_papers": collect_hf_papers,
    "github_trending": collect_github_trending,
    "venturebeat_ai": collect_venturebeat,
}

AI_SOURCE_LABELS = {
    "arxiv": "arXiv论文",
    "hf_papers": "HF每日论文",
    "github_trending": "GitHub趋势",
    "venturebeat_ai": "VentureBeat",
}
