"""国内热度榜采集器：微博热搜 / 百度热搜 / 头条热榜。

定位（引自《舆情与AI新闻采集来源总清单》第七章）：
    热榜是「情绪与热度」的免费一手层，反映**大众注意力分布**，
    与新闻报道（事实层）互为补充——一个事件能否成为博主的有效选题，
    既要看有没有报道，也要看有没有冲上热榜带来的自然流量。

三个源均为站点公开 JSON 接口（无需登录 Cookie），需注意：
  - 微博热搜**必须带 Referer**，否则返回 403；
  - 热榜条目是「关键词」而非完整标题，text 里补上榜单来源与排名，
    便于主题词典命中与事件命名；
  - 热度值（微博 num / 头条 HotValue）直接作为互动量参与热度计算，
    使热榜条目不再是「零互动」的空壳事件。
"""
from datetime import datetime, timezone

from .feedkit import _is_promo, _mk_item, clean_text, http_get_json

_HOT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _today() -> str:
    """热榜条目无精确发布时间，按采集当天标注（事件阶段判定需要时间）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_weibo_hot(keywords: list, top_n: int) -> tuple:
    """微博热搜榜（国内热度风向标，需 Referer 否则 403）。"""
    try:
        data = http_get_json("https://weibo.com/ajax/side/hotSearch", timeout=15,
                             headers={"Referer": "https://weibo.com/",
                                      "Accept": "application/json"})
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    rows = ((data or {}).get("data") or {}).get("realtime") or []
    items = []
    for i, r in enumerate(rows, 1):
        word = clean_text(r.get("word") or r.get("word_scheme") or "")
        if not word or not _is_hit(word):
            continue
        if _is_promo(word):
            continue
        num = int(r.get("num") or 0)
        label = clean_text(r.get("label_name") or "")
        items.append(_mk_item(
            platform="weibo",
            title=word,
            url=f"https://s.weibo.com/weibo?q={_q(word)}",
            author="微博热搜" + (f"·{label}" if label else ""),
            published=_today(),
            text=f"微博热搜第{i}位 · 热度{num}",
            metrics={"views": num, "likes": 0, "comments": 0, "reposts": 0,
                     "engagement_total": num, "rank": i},
        ))
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


def collect_baidu_hot(keywords: list, top_n: int) -> tuple:
    """百度热搜榜（PC 端 board 接口）。"""
    try:
        data = http_get_json("https://top.baidu.com/api/board?platform=wise&tab=realtime",
                             timeout=15, headers={"Referer": "https://top.baidu.com/board"})
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    items = []
    for c in _flatten_board(data):
        word = clean_text(c.get("word") or "")
        if not word or not _is_hit(word):
            continue
        if _is_promo(word):
            continue
        idx = int(c.get("index") or len(items) + 1)
        hot = int(c.get("hotScore") or 0)
        items.append(_mk_item(
            platform="baidu_hot",
            title=word,
            url=c.get("url") or f"https://www.baidu.com/s?wd={_q(word)}",
            author="百度热搜",
            published=_today(),
            text=f"百度热搜第{idx}位 · 热度{hot}",
            metrics={"views": hot, "likes": 0, "comments": 0, "reposts": 0,
                     "engagement_total": hot, "rank": idx},
        ))
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


def collect_toutiao_hot(keywords: list, top_n: int) -> tuple:
    """今日头条热榜（hot-board 公开接口，含 HotValue 热度值）。"""
    try:
        data = http_get_json(
            "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc", timeout=15)
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    items = []
    for i, r in enumerate(data.get("data") or [], 1):
        word = clean_text(r.get("Title") or "")
        if not word or not _is_hit(word):
            continue
        if _is_promo(word):
            continue
        hot = int(r.get("HotValue") or 0)
        items.append(_mk_item(
            platform="toutiao_hot",
            title=word,
            url=r.get("Url") or f"https://so.toutiao.com/search?keyword={_q(word)}",
            author="头条热榜",
            published=_today(),
            text=f"头条热榜第{i}位 · 热度{hot}",
            metrics={"views": hot, "likes": 0, "comments": 0, "reposts": 0,
                     "engagement_total": hot, "rank": i},
        ))
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


def _flatten_board(data: dict) -> list:
    """扁平化百度热搜返回。

    实际结构为 ``cards[].content[].content[]``（比预期多嵌一层），
    不同 tab 又会退化成 ``cards[].content[]``；这里两种都兼容，
    结构再变时安全返回空列表而不抛异常。
    """
    rows = []
    for card in ((data or {}).get("data") or {}).get("cards") or []:
        for c in card.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("word"):
                rows.append(c)
            inner = c.get("content")
            if isinstance(inner, list):
                rows.extend(x for x in inner if isinstance(x, dict) and x.get("word"))
    return rows


def _is_hit(text: str) -> bool:
    """热榜条目过短（多为四字关键词），用「词典命中 + 英文词边界」判定。

    刻意不用中文侧 ``"ai"`` 子串匹配——那会把 "air"、"said" 之类误判为 AI 相关；
    英文部分走带词边界的 ``_is_ai_related_en``。
    """
    from .lexicon import extract_topics
    from .overseas_collector import _is_ai_related_en
    return bool(extract_topics(text or "")) or _is_ai_related_en(text)


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


# ---------------------------------------------------------------- 注册表
HOTLIST_SOURCES = {
    "weibo_hot": collect_weibo_hot,
    "baidu_hot": collect_baidu_hot,
    "toutiao_hot": collect_toutiao_hot,
}

HOTLIST_SOURCE_LABELS = {
    "weibo_hot": "微博热搜",
    "baidu_hot": "百度热搜",
    "toutiao_hot": "头条热榜",
}
