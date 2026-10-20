"""中文原生 RSS 媒体源：量子位 / InfoQ。

定位（引自《舆情与AI新闻采集来源总清单》第五章 5.1、第八章 8.1）：
    中文 AI 媒体里**少数仍提供原生 RSS** 的就是量子位与 InfoQ。
    清单把「原生 RSS」列为稳定性最高的一层——它不依赖 RSSHub 自建、
    不受 SPA 改版影响，是中文舆情管道里最抗衰的一层。

    机器之心（原生 RSS 已失效，跳转 /data-service）、新智元、虎嗅
    （阿里云 WAF 拦截）、36氪（全站 SPA，任何路径都返回同一骨架）
    均已无法直接抓取，登记为 needs-rsshub，待自建 RSSHub 后再接。
"""
from .feedkit import _is_promo, _mk_item, clean_text, http_get
from .overseas_collector import _is_ai_any, _parse_feed


def _collect(name: str, url: str, platform: str, top_n: int) -> tuple:
    """通用原生 RSS 采集（中文媒体）。"""
    try:
        entries = _parse_feed(http_get(url, timeout=20, retries=1))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    items = []
    for e in entries:
        title = clean_text(e["title"])
        if not title or not _is_ai_any(title + " " + e["summary"]):
            continue
        if _is_promo(title):
            continue
        items.append(_mk_item(
            platform=platform,
            title=title,
            url=e["url"],
            author=e["author"],
            published=e["published"],
            text=clean_text(e["summary"])[:300],
        ))
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


def collect_qbitai(keywords: list, top_n: int) -> tuple:
    """量子位（中文 AI 媒体，原生 RSS）。"""
    return _collect("qbitai", "https://www.qbitai.com/feed", "qbitai", top_n)


def collect_infoq(keywords: list, top_n: int) -> tuple:
    """InfoQ 中文（原生 RSS，偏工程与架构）。"""
    return _collect("infoq", "https://www.infoq.cn/feed", "infoq", top_n)


MEDIA_SOURCES = {
    "qbitai": collect_qbitai,
    "infoq": collect_infoq,
}

MEDIA_SOURCE_LABELS = {
    "qbitai": "量子位",
    "infoq": "InfoQ",
}
