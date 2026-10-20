"""新闻事件级聚类：把舆情条目按「同一件事」聚成具体事件。

早期版本按话题分组（同话题内按 72h 切波次），带来两个问题：
  1. 一条新闻命中 N 个话题 → 生成 N 个事件，事件库里出现重复舆情
     （如 REA 新闻同时命中 REA/MCP协议/智能体/AI编程 → 4 个同名事件）
  2. 宽泛话题（智能体/大模型）会把不相干条目硬凑成一个「事件」，
     事件名取其中某条，导致「看不出来舆情事件是什么」

现改为**故事级聚类**：先用 dedup.cluster_stories 把互为同一件事的条目
连通成一簇（IDF 余弦相似度 + 72h 时间窗），一簇 = 一个事件；
话题退化为事件的**属性**（取簇内条目话题并集），不再决定事件身份。
"""
from collections import defaultdict
from datetime import datetime

from .dedup import cluster_stories, pick_topic
from .feedkit import _parse_dt, _phase_of
from .source_registry import SIGNAL_CLASSES, class_of

EVENT_GAP_HOURS = 72      # 同故事允许的最大发布间隔（小时）


def _disp(it: dict) -> str:
    """条目展示名：优先中文译名（title_zh），无则原文。"""
    return (it.get("title_zh") or "").strip() or it.get("title_or_text") or ""


def _topic_union(members: list) -> list:
    """簇内条目话题并集（按出现频次降序，保证顺序稳定）。"""
    cnt = defaultdict(int)
    for it in members:
        for t in set(it.get("topics") or []):
            cnt[t] += 1
    return [t for t, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]


def _event_of_story(members: list, cfg: dict | None = None) -> dict:
    """一个故事簇 → 一个事件（话题只作为属性，不再决定事件身份）。"""
    times = sorted(t for t in (_parse_dt(it.get("published_at")) for it in members) if t)
    engs = [int((it.get("metrics") or {}).get("engagement_total") or
                (it.get("metrics") or {}).get("likes", 0) +
                (it.get("metrics") or {}).get("comments", 0)) for it in members]
    # 代表标题：有互动数据取最热条目；无互动数据（RSS/搜索源）取**时间最早**的
    # 条目——新闻事件由首发报道命名，避免任意条目（如教程视频）当事件名
    if max(engs, default=0) > 0:
        top = max(members, key=lambda x: int((x.get("metrics") or {}).get("engagement_total") or 0))
    else:
        dated = [it for it in members if _parse_dt(it.get("published_at"))]
        top = min(dated or members,
                  key=lambda x: _parse_dt(x.get("published_at")) or datetime.max)
    items_meta = sorted(
        ({"title": _disp(it),
          "published_at": it.get("published_at") or "",
          "platform": it.get("platform") or "",
          "url": it.get("url") or ""} for it in members),
        key=lambda m: m["published_at"] or "")[:40]
    primary = pick_topic(members)
    classes = sorted({class_of(it.get("platform", "")) for it in members})
    return {
        "id": "",
        "topic": primary,
        "title": _disp(top),
        "item_titles": [_disp(it) for it in members],
        "phase": _phase_of(times),
        "count": len(members),
        "topics": _topic_union(members),
        "avg_eng": round(sum(engs) / len(engs)) if engs else 0,
        "max_eng": max(engs) if engs else 0,
        "sentiment": {s: sum(1 for it in members if it.get("sentiment", "neutral") == s)
                      for s in {it.get("sentiment", "neutral") for it in members}},
        "platforms": sorted({it.get("platform", "") for it in members}),
        "classes": classes,
        "is_signal": bool(classes) and all(c in SIGNAL_CLASSES for c in classes),
        "first_seen": times[0].isoformat(timespec="seconds") if times else "",
        "last_seen": times[-1].isoformat(timespec="seconds") if times else "",
        "items_meta": items_meta,
    }


def build_news_events(items: list, cfg: dict | None = None,
                      gap_hours: int = EVENT_GAP_HOURS) -> list:
    """把舆情条目聚合为新闻事件（故事级聚类，消除重复）。

    返回按热度排序的事件列表，id 为 E01/E02…（E=Event）。
    """
    groups = cluster_stories(items, cfg)
    events = [_event_of_story(g, cfg) for g in groups if g]
    events.sort(key=lambda e: -(e["avg_eng"] * 0.6 + e["max_eng"] * 0.4))
    for i, ev in enumerate(events, 1):
        ev["id"] = f"E{i:02d}"
    return events
