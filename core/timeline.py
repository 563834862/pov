"""博主跟进辅助：滞后计算 + 响应速度榜。

对命中舆情事件的博主内容，计算「舆情发布 → 博主跟进」的滞后时间。

滞后基准（每条帖子独立计算）：
  帖子发布前最近的一条舆情条目（含 24h 宽限）——直接衡量「舆情发布后
  多久跟进」：帖子 Oct 10 发布、事件内最新舆情 Oct 7 → 滞后 3 天。
  帖子早于事件所有条目时取最早条目，滞后为负 = 提前布局（比本轮舆情
  更早涉及该话题）。

输出：
  blogger_response_speed(posts, events) —— 博主维度：追热响应速度榜
  build_event_timelines(posts, events)  —— [已废弃 v0.4.3] 时间线看板已删除，
      跟进关系改由「舆情事件库」API GET /api/event?id=E03 点击查看

供 web/server.py 的 /api/event 详情接口复用：_post_ref / _follower_entry / fmt_lag。
"""
from collections import defaultdict
from datetime import datetime, timezone

VERDICT_COLOR = {"踩中热点": "#16a34a", "追热稳健": "#d97706", "追热偏早": "#2563eb",
                 "追热偏晚": "#d97706", "选题滞后": "#dc2626", "独苗选题": "#6b7280",
                 "数据不足": "#6b7280"}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)   # 事件 first_seen 为 naive UTC
    return dt


def fmt_lag(hours) -> str:
    """滞后时长人话显示；负值=博主抢在舆情发布之前（提前布局）。"""
    if hours is None:
        return "-"
    if hours < 0:
        h = abs(hours)
        if h < 1:
            return f"提前 {h * 60:.0f} 分钟"
        if h < 48:
            return f"提前 {h:.1f} 小时"
        return f"提前 {h / 24:.0f} 天"
    if hours < 1:
        return f"{hours * 60:.0f} 分钟"
    if hours < 48:
        return f"{hours:.1f} 小时"
    return f"{hours / 24:.1f} 天"


def _cst(iso: str) -> str:
    """转东八区显示（与报告一致）。"""
    from datetime import timedelta
    dt = _parse(iso)
    if not dt:
        return "-"
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")


def _post_ref(post: dict, ev_items: list):
    """确定帖子的滞后基准：帖子发布前最近的一条舆情条目（含 24h 宽限）。

    —— 直接衡量「舆情发布后多久跟进」：帖子 Oct 10 发布、事件内最新舆情
    Oct 7 → 滞后 3 天。帖子早于事件所有条目时取最早条目，滞后为负
    （博主比本轮舆情更早涉及该话题 = 提前布局）。
    返回 (ref_dt, ref_title, basis)——basis: "news" | "topic"
    """
    from datetime import timedelta
    me = (post.get("verification") or {}).get("matched_event") or {}
    dated = sorted(((_parse(it.get("published_at")), it) for it in (ev_items or [])),
                   key=lambda x: x[0] or _EPOCH)
    dated = [(d, it) for d, it in dated if d]
    pub = _parse(post.get("published_at"))
    if dated and pub:
        grace = pub + timedelta(hours=24)
        preceding = [(d, it) for d, it in dated if d <= grace]
        if preceding:
            d, it = preceding[-1]
            return d, it.get("title") or "", "news"
    if dated:
        d, it = dated[0]
        return d, it.get("title") or "", "topic"
    return _parse(me.get("first_seen")), "", "topic"


def _follower_entry(post: dict, ref_dt, ref_title: str, basis: str) -> dict:
    v = post.get("verification") or {}
    pub = _parse(post.get("published_at"))
    lag_h = (round((pub - ref_dt).total_seconds() / 3600, 1)
             if (pub and ref_dt) else None)
    return {
        "blogger": post.get("blogger") or "",
        "platform": post.get("platform") or "",
        "title": (post.get("title") or "")[:80],
        "url": post.get("url") or "",
        "published_at": post.get("published_at") or "",
        "published_cst": _cst(post.get("published_at")),
        "lag_hours": lag_h,
        "lag_text": fmt_lag(lag_h),
        "early": bool(lag_h is not None and lag_h < 0),
        "lag_basis": basis,                 # news=绑定具体新闻 / topic=话题首发
        "ref_title": (ref_title or "")[:60],
        "verdict": v.get("verdict") or "",
        "emoji": v.get("emoji") or "",
        "total": v.get("total"),
        "color": VERDICT_COLOR.get(v.get("verdict"), "#6b7280"),
    }


def build_event_timelines(posts: list, events: list | None = None,
                          top_n: int = 15) -> list:
    """[已废弃 v0.4.3] 事件维度时间线看板已删除，跟进关系改由「舆情事件库」
    GET /api/event?id=E03 点击查看。保留此函数仅为兼容旧数据引用。"""
    return []


def blogger_response_speed(posts: list, events: list | None = None,
                           min_events: int = 2) -> list:
    """博主维度：追热响应速度榜（仅统计舆情发布后的跟进，负滞后单列）。"""
    ev_items = {ev["id"]: ev.get("items_meta") or []
                for ev in (events or []) if ev.get("id")}
    by_blogger = defaultdict(lambda: {"after": [], "before": 0})
    for p in posts:
        me = (p.get("verification") or {}).get("matched_event")
        if not me or not me.get("id"):
            continue
        ref_dt, _, _ = _post_ref(p, ev_items.get(me["id"]) or [])
        pub = _parse(p.get("published_at"))
        if not (ref_dt and pub):
            continue
        lag = (pub - ref_dt).total_seconds() / 3600
        name = p.get("blogger") or ""
        if lag >= 0:
            by_blogger[name]["after"].append(lag)
        else:
            by_blogger[name]["before"] += 1

    rows = []
    for name, d in by_blogger.items():
        total = len(d["after"]) + d["before"]
        if total < min_events:
            continue
        after = d["after"]
        rows.append({
            "blogger": name,
            "events": total,
            "avg_lag_hours": round(sum(after) / len(after), 1) if after else None,
            "avg_lag_text": fmt_lag(round(sum(after) / len(after), 1)) if after else "仅提前布局",
            "fastest_lag_text": fmt_lag(round(min(after), 1)) if after else "-",
            "early_count": d["before"],          # 抢在舆情发布前（提前布局）
            "within24h": sum(1 for x in after if x < 24),
        })
    rows.sort(key=lambda r: (r["avg_lag_hours"] is None, r["avg_lag_hours"] or 0))
    return rows
