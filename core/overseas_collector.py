"""海外舆情采集器：X / YouTube / HackerNews / ProductHunt / 海外科技媒体。

网络现实（2026-10-17 本机实测）：
  - x.com / twitter.com / youtube.com / reddit.com / trends.google.com
    域名被网络策略拦截（连接直接失败，走系统代理也不通）
  - hn.algolia.com / producthunt.com / techcrunch.com / theverge.com /
    arstechnica.com / technologyreview.com / openai.com 可直连
    （注意：必须绕过系统代理，requests 默认走环境代理会 502/超时）

  → X / YouTube 采用「WebSearch 喂入」模式：由 Agent 用 WebSearch 检索
    X 推文 / YouTube 视频，结果按 search-*.json 同款格式放入
    data/inputs/oversea-x-*.json 与 data/inputs/oversea-yt-*.json，
    采集器读取后映射为 x / youtube 平台条目。
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import storage
from .feedkit import UA, _is_ai_related, _is_case_study, _is_promo, _mk_item

# 英文 AI 相关性过滤（词边界匹配，避免 "ai" 子串误伤 said/chain 等）
EN_AI_PATTERNS = [
    r"\bAI\b", r"\bAIGC\b", r"\bartificial intelligence\b", r"\bLLMs?\b", r"\bGPT\b",
    r"\bOpenAI\b", r"\bAnthropic\b", r"\bClaude\b", r"\bGemini\b", r"\bDeepSeek\b",
    r"\bSora\b", r"\bMidjourney\b", r"\bCopilot\b", r"\bCursor\b", r"\bSuno\b",
    r"\bManus\b", r"\bAI agents?\b", r"\bagentic\b", r"\bgenerative\b",
    r"\btext-to-(video|image|speech|3d)\b", r"\bvideo generation\b",
    r"\bimage generation\b", r"\bstable diffusion\b", r"\bFLUX\b", r"\bQwen\b",
    r"\bKimi\b", r"\bGrok\b", r"\bLlama\b", r"\bmachine learning\b",
    r"\bneural network", r"\bfine-tun", r"\bmultimodal\b", r"\brobot",
    r"\bHugging ?Face\b", r"\bPerplexity\b", r"\bRunway\b", r"\bPika\b",
    r"\bVEO\b", r"\bSeedance\b", r"\bMiniMax\b", r"\bComfyUI\b",
    r"\bopen[- ]weights?\b", r"\bfrontier model",
    # 科技前沿热点（用户关注面：马斯克/特斯拉/SpaceX）
    r"\bTesla\b", r"\bMusk\b", r"\bSpaceX\b", r"\bRoadster\b",
    r"\bautonomous driv", r"\bOptimus\b", r"\bCybertruck\b",
]
EN_AI_RE = re.compile("|".join(EN_AI_PATTERNS), re.IGNORECASE)


def _is_ai_related_en(text: str) -> bool:
    return bool(EN_AI_RE.search(text or ""))


def _is_ai_any(text: str) -> bool:
    """中英文任一命中即算领域舆情（海外源含少量中文报道）。"""
    return _is_ai_related(text) or _is_ai_related_en(text)


def _fetch(url: str, timeout=15) -> str:
    """直连抓取（绕过系统代理：requests 信任环境代理，本机代理会拦截海外站）。"""
    import requests
    session = requests.Session()
    session.trust_env = False          # 关键：不走 http_proxy 环境变量
    r = session.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _iso(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- RSS / Atom 通用解析
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _text(el, path: str) -> str:
    if el is None:
        return ""
    node = el.find(path, _NS)
    if node is None:
        # 无命名空间兜底（部分源用默认 ns）
        node = el.find(path.replace("atom:", "").replace("dc:", ""))
    return (node.text or "").strip() if node is not None else ""


def _parse_feed(xml_text: str) -> list:
    """解析 RSS2.0 / Atom，返回 [{title, url, published, author, summary}]。"""
    root = ET.fromstring(xml_text)
    entries = []
    # Atom
    for e in root.findall(".//atom:entry", _NS):
        link = ""
        for l in e.findall("atom:link", _NS):
            if l.get("rel", "alternate") in ("", "alternate") and l.get("href"):
                link = l.get("href")
                break
        pub = _text(e, "atom:published") or _text(e, "atom:updated")
        entries.append({
            "title": _text(e, "atom:title"),
            "url": link,
            "published": _iso(_coerce_dt(pub)),
            "author": _text(e, "atom:author/atom:name") or _text(e, "dc:creator"),
            "summary": _text(e, "atom:summary") or _text(e, "atom:content"),
        })
    # RSS 2.0
    for it in root.findall(".//item"):
        entries.append({
            "title": (it.findtext("title") or "").strip(),
            "url": (it.findtext("link") or "").strip(),
            "published": _iso(_coerce_dt((it.findtext("pubDate") or "").strip())),
            "author": (it.findtext("dc:creator", namespaces=_NS) or "").strip(),
            "summary": (it.findtext("description") or "")[:400],
        })
    return entries


def _coerce_dt(s: str):
    if not s:
        return None
    s = str(s).strip()
    try:
        return parsedate_to_datetime(s)          # RFC822（pubDate）
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))   # ISO8601
    except ValueError:
        return None


def collect_rss(name: str, url: str, platform: str, keywords: list, top_n: int) -> tuple:
    """通用 RSS/Atom 采集适配器（海外科技媒体/AI 官方博客）。

    过滤：AI 相关性 + 厂商客户案例/效果营销稿（"How X cuts costs with ChatGPT"
    类属市场物料，不是舆情热点，剔除以免稀释事件浓度）。
    """
    items = []
    try:
        entries = _parse_feed(_fetch(url))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    for e in entries:
        if not e["title"] or not _is_ai_any(e["title"] + " " + e["summary"]):
            continue
        if _is_case_study(e["title"]):
            continue
        items.append(_mk_item(
            platform=platform,
            title=e["title"],
            url=e["url"],
            author=e["author"],
            published=e["published"],
            text=e["summary"],
            metrics={"views": 0, "likes": 0, "comments": 0, "reposts": 0,
                     "engagement_total": 0, "rank": None},
        ))
    # 按发布时间倒序（新的在前）
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:top_n * 2], ("ok" if items else "empty")


# ---------------------------------------------------------------- HackerNews
def collect_hackernews(keywords: list, top_n: int) -> tuple:
    """HN 首页（Algolia API，海外开发者社区风向标）。"""
    items = []
    try:
        import requests
        session = requests.Session()
        session.trust_env = False
        r = session.get("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50",
                        headers={"User-Agent": UA}, timeout=15)
        data = r.json()
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    for h in data.get("hits") or []:
        title = h.get("title") or ""
        if not _is_ai_any(title + " " + (h.get("story_text") or "")):
            continue
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        points = int(h.get("points") or 0)
        comments = int(h.get("num_comments") or 0)
        items.append(_mk_item(
            platform="hackernews",
            title=title,
            url=url,
            author=h.get("author") or "",
            published=_iso(_coerce_dt(h.get("created_at") or "")),
            metrics={"views": 0, "likes": points, "comments": comments, "reposts": 0,
                     "engagement_total": points + comments, "rank": None},
        ))
    items.sort(key=lambda x: -int((x.get("metrics") or {}).get("engagement_total") or 0))
    return items[:top_n * 2], ("ok" if items else "empty")


# ---------------------------------------------------------------- ProductHunt
def collect_producthunt(keywords: list, top_n: int) -> tuple:
    """ProductHunt 每日新品（海外 AI 产品首发风向标）。

    只保留**标题本身**即 AI 相关的条目——PH 大量新品是靠摘要里的
    "AI-powered" 蹭过过滤的泛工具（笔记/待办/terminal 等），标题无关
    即淘汰，避免每日新品刷成假事件。
    """
    items = []
    try:
        entries = _parse_feed(_fetch("https://www.producthunt.com/feed"))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    for e in entries:
        if not e["title"] or not _is_ai_any(e["title"]):
            continue
        if _is_case_study(e["title"]):
            continue
        items.append(_mk_item(
            platform="producthunt",
            title=e["title"],
            url=e["url"],
            author=e["author"],
            published=e["published"],
            text=e["summary"],
        ))
    return items[:top_n * 2], ("ok" if items else "empty")


# ---------------------------------------------------------------- X / YouTube（WebSearch 喂入）
def _collect_oversea_input(glob_pat: str, platform: str, top_n: int) -> tuple:
    files = sorted(storage.INPUTS.glob(glob_pat), reverse=True)
    used = files[:2]
    items = []
    for fp in used:
        for i, r in enumerate(storage.read_json(fp, default=[]) or []):
            title = r.get("title") or ""
            if not _is_ai_any(title + " " + r.get("snippet", "")):
                continue
            base_url = (r.get("url") or "").split("#")[0]
            items.append(_mk_item(
                platform=platform,
                title=title,
                url=f"{base_url}#s{i}" if base_url else "",
                author=r.get("source") or "",
                published=r.get("date") or "",
                text=r.get("snippet") or "",
            ))
    if not items:
        return [], ("no-input" if not used else "filtered-empty")
    return items[:top_n * 2], "ok"


def collect_x_search(keywords: list, top_n: int) -> tuple:
    """X（Twitter）平台舆情 —— WebSearch 喂入 data/inputs/oversea-x-*.json。

    x.com 在本机网络不可直连，由 Agent WebSearch 检索 X 内容后喂入。
    """
    return _collect_oversea_input("oversea-x-*.json", "x", top_n)


def collect_youtube_search(keywords: list, top_n: int) -> tuple:
    """YouTube 平台舆情 —— WebSearch 喂入 data/inputs/oversea-yt-*.json。

    youtube.com 在本机网络不可直连，由 Agent WebSearch 检索后喂入。
    """
    return _collect_oversea_input("oversea-yt-*.json", "youtube", top_n)


# ---------------------------------------------------------------- 注册表
OVERSEA_SOURCES = {
    "x_search": collect_x_search,                    # X 平台（WebSearch 喂入）
    "youtube_search": collect_youtube_search,        # YouTube 平台（WebSearch 喂入）
    "hackernews": collect_hackernews,                # HN 首页
    "producthunt": collect_producthunt,              # ProductHunt 新品
    "techcrunch_ai": lambda kw, n: collect_rss(
        "techcrunch", "https://techcrunch.com/category/artificial-intelligence/feed/",
        "techcrunch", kw, n),                        # TechCrunch AI
    "theverge_ai": lambda kw, n: collect_rss(
        "theverge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "theverge", kw, n),                          # The Verge AI
    "arstechnica": lambda kw, n: collect_rss(
        "arstechnica", "https://feeds.arstechnica.com/arstechnica/index",
        "arstechnica", kw, n),                       # Ars Technica
    "mittr": lambda kw, n: collect_rss(
        "mittr", "https://www.technologyreview.com/feed/",
        "mittr", kw, n),                             # MIT Technology Review
    "openai_news": lambda kw, n: collect_rss(
        "openai", "https://openai.com/news/rss.xml",
        "openai", kw, n),                            # OpenAI 官方新闻
}

# 源说明（用于看板/报告展示）
OVERSEA_SOURCE_LABELS = {
    "x_search": "X平台", "youtube_search": "YouTube",
    "hackernews": "HackerNews", "producthunt": "ProductHunt",
    "techcrunch_ai": "TechCrunch", "theverge_ai": "TheVerge",
    "arstechnica": "ArsTechnica", "mittr": "MIT科技评论", "openai_news": "OpenAI官方",
}
