"""自我进化：博主内容反推采集缺口 → 自动补词/补关键词 → 闭环验证。

核心思想（用户反馈驱动）：
  博主视频是热点舆情的「下游信号」。如果多位博主近期都在讲某个话题，
  而舆情基线里却没有对应事件——这不是博主「独苗」，而是**采集缺口**。
  本模块把这种信号变成闭环动作：

    detect  从未匹配/低分匹配的领域内博主内容反推话题缺口
    evolve  缺口话题 → data/evolved_keywords.json（下轮采集自动生效）
            高频新实体词 → data/dynamic_lexicon.json（词典实时学习）
    resolve 下轮采集后检查缺口是否被新事件覆盖 → resolved；
            连续 stale_rounds 轮未覆盖 → stale（判定教程类/源不覆盖，停止追补）

缺口类型：
  no_event    ≥2 位博主讲同一话题，基线无该话题事件      → 采集缺口（补关键词）
  thin_event  有事件但报道覆盖薄（count≤2），博主 ≥3 人   → 覆盖不足（补关键词）
  stale_event 有事件但首发时间早于基线时间窗（旧闻）      → 重新采集
  new_entity  高频新实体词（产品名候选）不在词典          → 动态词库学习
  tutorial    标题带教程/整活特征（三分钟/手把手/攻略…）  → 常青内容，只标记不追补

文件：
  data/evolved_keywords.json  进化关键词状态机
  data/dynamic_lexicon.json   进化学到的产品别名（lexicon.extract_topics 实时合并）
"""
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import storage
from .lexicon import extract_topics, LEXICON, PRODUCT_LEXICON
from .feedkit import _parse_dt

EVO_FILE = storage.DATA / "evolved_keywords.json"
DYN_FILE = storage.DATA / "dynamic_lexicon.json"

DEFAULTS = {
    "enabled": True,
    "min_bloggers": 2,          # 判定采集缺口的最少博主数（同话题）
    "thin_event_max_count": 2,  # 事件报道条数 ≤ 此值视为覆盖薄
    "stale_rounds": 3,          # 连续多少轮未覆盖 → 判定 stale 停止追补
    "max_active_keywords": 12,  # 并发生效的进化关键词上限（防采集请求膨胀）
}

# 教程/常青内容特征：这些内容不是追新闻，匹配不上事件是正常的
TUTORIAL_PAT = re.compile(
    r"教程|手把手|攻略|干货|小白|零基础|零代码|三分钟|两分钟|分钟[做个做款开]|"
    r"怎么做|如何|入门|上手|拆解(?!AI)|盘点|横测|实测|大赏|整活|挑战|"
    r"第[一二三四五六七八九十\d]+期|合集|课程|训练营")

# 新实体词候选的噪声过滤：通用词/停用词不学
_ENTITY_STOP = {
    "vibe", "coding", "app", "apps", "demo", "agent", "agents", "http", "https",
    "www", "com", "html", "python", "javascript", "google", "apple", "视频",
    "图片", "内容", "平台", "工具", "软件", "网站", "项目", "功能", "效果",
}


def _cfg(cfg: dict) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("evolution") or {})}


# ---------------- 动态词库（lexicon 实时学习） ----------------

def load_dynamic_lexicon() -> dict:
    try:
        d = json.loads(DYN_FILE.read_text(encoding="utf-8"))
        return {k: [str(a) for a in v] for k, v in d.items()
                if isinstance(v, list) and v}
    except (OSError, json.JSONDecodeError):
        return {}


def save_dynamic_lexicon(d: dict) -> None:
    storage.write_json(DYN_FILE, d)


def _known_aliases() -> set:
    """词典已有别名（含动态词库），避免重复学习。"""
    known = set()
    for als in list(LEXICON.values()) + list(load_dynamic_lexicon().values()):
        for a in als:
            known.add(re.sub(r"\s+", "", (a or "").lower()))
    return known


def learn_entities(candidates: Counter, min_count: int = 2) -> list:
    """把高频新实体词学进动态词库。返回新学到的 {canonical, aliases} 列表。"""
    known = _known_aliases()
    dyn = load_dynamic_lexicon()
    learned = []
    for term, cnt in candidates.most_common():
        if cnt < min_count:
            continue
        t = term.strip()
        low = re.sub(r"\s+", "", t.lower())
        if not t or low in known or low in _ENTITY_STOP:
            continue
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", t):
            continue
        if len(t) > 24:
            continue
        if t in dyn:            # 已学过：补计数即可
            continue
        dyn[t] = [t, low] if low != t.lower() else [t]
        learned.append({"canonical": t, "count": cnt})
    if learned:
        save_dynamic_lexicon(dyn)
        storage.log(f"[evolution] 动态词库新学 {len(learnered)} 个实体词: "
                    f"{[x['canonical'] for x in learned][:8]}")
    return learned


# ---------------- 进化关键词状态机 ----------------

def load_evolved() -> dict:
    try:
        d = json.loads(EVO_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_evolved(d: dict) -> None:
    storage.write_json(EVO_FILE, d)


def active_keywords() -> list:
    """当前生效的进化关键词（采集时合并进 cfg['keywords']）。"""
    ev = load_evolved()
    return [k for k, v in ev.items()
            if v.get("status") in ("pending", "active")]


def _term_candidates(text: str) -> set:
    """从帖子文本提取新实体词候选（中英文混合，过滤停用）。"""
    from .verify import _salient_terms
    terms = set()
    for t in _salient_terms(text or ""):
        tl = t.lower()
        if tl in _ENTITY_STOP or len(tl) < 2:
            continue
        # 英文词要求含大写形式（产品名特征）或长度≥3
        if t.isascii():
            if not (any(c.isupper() for c in t) or len(t) >= 4):
                continue
        terms.add(t)
    return terms


# ---------------- 缺口检测 ----------------

def detect_gaps(posts: list, baseline: dict, cfg: dict) -> list:
    """从领域内未匹配/低分匹配的博主内容反推采集缺口。

    返回缺口列表（按强度降序）：
      {topic/gap_type, bloggers:[...], post_count, sample, reason,
       event_count, status_hint}
    """
    c = _cfg(cfg)
    if not c.get("enabled", True):
        return []
    events = [e for e in (baseline.get("pov_events") or []) if not e.get("is_signal")]
    ev_by_topic = {}
    for e in events:
        for t in (e.get("topics") or []):
            ev_by_topic.setdefault(t, []).append(e)
    range_days = int((cfg.get("baseline") or {}).get("range", "7d").rstrip("d"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=range_days)

    # 领域内未命中（或低分命中）的帖子，按话题聚合
    by_topic = {}
    entity_cnt = Counter()
    for p in posts:
        v = p.get("verification") or {}
        if v.get("verdict") not in ("独苗选题", "选题滞后"):
            continue
        topics = v.get("post_topics") or p.get("topics") or []
        if not topics:
            continue                      # 领域外内容，不算采集缺口
        matched = v.get("matched_event") or {}
        if matched and v.get("total") is not None and v.get("total") >= 50:
            continue                      # 中高分命中：不是缺口
        title = (p.get("title") or "")[:60]
        for t in topics:
            d = by_topic.setdefault(t, {"topic": t, "bloggers": set(), "posts": 0,
                                        "samples": [], "max_eng": 0,
                                        "tutorial": 0})
            d["posts"] += 1
            if p.get("blogger"):
                d["bloggers"].add(p["blogger"])
            if len(d["samples"]) < 3 and title:
                d["samples"].append(title)
            m = p.get("metrics") or {}
            d["max_eng"] = max(d["max_eng"], int(m.get("likes", 0) + m.get("comments", 0)))
            if TUTORIAL_PAT.search((p.get("title") or "") + (p.get("text") or "")):
                d["tutorial"] += 1
        # 新实体词候选：只从领域内帖子提取（保证 AI 相关性）
        for t in _term_candidates((p.get("title") or "") + " " + (p.get("text") or "")):
            entity_cnt[t] += 1

    gaps = []
    for t, d in by_topic.items():
        nblog = len(d["bloggers"])
        evs = ev_by_topic.get(t) or []
        fresh_evs = [e for e in evs
                     if (_parse_dt(e.get("first_seen")) or cutoff) >= cutoff]
        is_tutorial = d["tutorial"] >= max(1, d["posts"] // 2)
        if not evs:
            gap_type = "no_event"
            reason = f"{nblog} 位博主在讲，基线无「{t}」任何事件"
        elif not fresh_evs:
            gap_type = "stale_event"
            reason = f"「{t}」事件首发早于基线时间窗（{range_days}d），博主讲的是新一波"
        elif len(evs) <= c["thin_event_max_count"] and nblog >= 3:
            gap_type = "thin_event"
            reason = (f"「{t}」仅 {len(evs)} 条报道，却有 {nblog} 位博主在讲，"
                      f"舆情覆盖明显滞后于博主圈")
        else:
            continue                      # 事件覆盖正常，不是缺口
        if is_tutorial:
            gap_type = "tutorial"
            reason += "；标题带教程/整活特征，判定常青内容（不追补）"
        gaps.append({
            "topic": t, "gap_type": gap_type, "bloggers": sorted(d["bloggers"]),
            "blogger_count": nblog, "post_count": d["posts"],
            "samples": d["samples"], "max_eng": d["max_eng"],
            "event_count": len(evs), "reason": reason,
            "strength": nblog * 2 + d["posts"],
        })

    # 新实体词候选（跨帖子聚合，不进 gaps 主列表，单独返回）
    new_entities = [{"term": t, "count": n} for t, n in entity_cnt.most_common(30)]
    gaps.sort(key=lambda g: -g["strength"])
    return gaps, new_entities


# ---------------- 进化执行 ----------------

def evolve(gaps: list, new_entities: list, cfg: dict) -> dict:
    """把缺口写进状态机；新实体词学进动态词库。返回本次动作摘要。"""
    c = _cfg(cfg)
    if not c.get("enabled", True):
        return {"actions": [], "learned": []}
    ev = load_evolved()
    now = storage.now_iso()
    actions = []
    for g in gaps:
        if g["gap_type"] in ("tutorial",):
            key = g["topic"]
            if key not in ev:
                ev[key] = {"status": "stale", "gap_type": "tutorial",
                           "first_seen": now, "last_seen": now, "miss_rounds": 0,
                           "bloggers": g["blogger_count"], "reason": g["reason"]}
            continue
        if g["blogger_count"] < c["min_bloggers"] and g["gap_type"] != "no_event":
            continue
        key = g["topic"]
        cur = ev.get(key)
        if cur and cur.get("status") in ("resolved", "stale"):
            continue                      # 已结案的不重启
        if cur:
            cur["last_seen"] = now
            cur["bloggers"] = g["blogger_count"]
            cur["reason"] = g["reason"]
            cur["gap_type"] = g["gap_type"]
            actions.append({"topic": key, "action": "keep", "gap_type": g["gap_type"]})
        else:
            ev[key] = {"status": "pending", "gap_type": g["gap_type"],
                       "first_seen": now, "last_seen": now, "miss_rounds": 0,
                       "bloggers": g["blogger_count"], "reason": g["reason"]}
            actions.append({"topic": key, "action": "add", "gap_type": g["gap_type"]})
    # 上限保护：pending/active 超量时，优先保留强度高的（这里按博主数近似）
    live = {k: v for k, v in ev.items() if v.get("status") in ("pending", "active")}
    if len(live) > c["max_active_keywords"]:
        for k in sorted(live, key=lambda k: -live[k].get("bloggers", 0))[c["max_active_keywords"]:]:
            ev[k]["status"] = "stale"
            ev[k]["reason"] = "超出进化关键词上限，暂停追补"
            actions.append({"topic": k, "action": "cap_stale"})
    if actions:
        save_evolved(ev)

    learned = learn_entities(Counter({e["term"]: e["count"] for e in new_entities}))
    return {"actions": actions, "learned": learned}


def resolve_gaps(baseline: dict, cfg: dict) -> dict:
    """闭环验证：检查 pending/active 缺口是否已被新事件覆盖。"""
    c = _cfg(cfg)
    ev = load_evolved()
    if not ev:
        return {"resolved": [], "stale": [], "pending": []}
    events = [e for e in (baseline.get("pov_events") or []) if not e.get("is_signal")]
    ev_topics = set()
    for e in events:
        ev_topics.update(e.get("topics") or [])
    # 动态词库学到的实体词也要能匹配事件标题
    dyn = load_dynamic_lexicon()
    ev_text = " ".join(" ".join(e.get("item_titles") or [e.get("title") or ""])
                       for e in events).lower()

    resolved, went_stale, still = [], [], []
    changed = False
    now = storage.now_iso()
    for k, v in ev.items():
        if v.get("status") not in ("pending", "active"):
            continue
        hit = k in ev_topics
        if not hit:
            for alias in dyn.get(k, []):
                if alias.lower() in ev_text:
                    hit = True
                    break
        if hit:
            v["status"] = "resolved"
            v["resolved_at"] = now
            resolved.append(k)
            changed = True
        else:
            v["miss_rounds"] = int(v.get("miss_rounds", 0)) + 1
            if v["miss_rounds"] >= c["stale_rounds"]:
                v["status"] = "stale"
                v["reason"] = (v.get("reason") or "") + \
                    f"；连续 {v['miss_rounds']} 轮未采集到覆盖，判定源不覆盖或教程类，停止追补"
                went_stale.append(k)
            else:
                still.append(k)
            changed = True
    if changed:
        save_evolved(ev)
    if resolved:
        storage.log(f"[evolution] 缺口闭环：{resolved} 已被新采集覆盖")
    if went_stale:
        storage.log(f"[evolution] 缺口停止追补：{went_stale}")
    return {"resolved": resolved, "stale": went_stale, "pending": still}


# ---------------- 单轮闭环 ----------------

def attach_gaps(posts: list, gaps: list, ev: dict) -> int:
    """把缺口信息回写到领域内未命中帖子的 verification，让报告/看板可解释。"""
    gap_by_topic = {g["topic"]: g for g in gaps}
    n = 0
    for p in posts:
        v = p.get("verification") or {}
        if v.get("verdict") != "独苗选题":
            continue
        topics = v.get("post_topics") or []
        hit = next((gap_by_topic[t] for t in topics if t in gap_by_topic), None)
        if not hit:
            continue
        rec = ev.get(hit["topic"]) or {}
        v["gap"] = {
            "topic": hit["topic"], "gap_type": hit["gap_type"],
            "blogger_count": hit["blogger_count"],
            "status": rec.get("status", "pending"),
            "reason": hit["reason"],
        }
        if hit["gap_type"] == "tutorial":
            v["reason"] = (f"未命中舆情事件，但属「{hit['topic']}」常青教程内容"
                           f"（{hit['blogger_count']} 位博主均在产出），非采集缺口")
        else:
            v["reason"] = (f"疑似采集缺口：{hit['blogger_count']} 位博主在讲「{hit['topic']}」"
                           f"但基线无对应事件（{rec.get('status', 'pending')}），"
                           f"已自动补充采集关键词")
        n += 1
    return n


def run_cycle(posts: list, baseline: dict, cfg: dict) -> dict:
    """每轮验证后调用：检测缺口 → 进化 → 闭环验证 → 回写帖子。"""
    c = _cfg(cfg)
    if not c.get("enabled", True):
        return {"enabled": False}
    gaps, new_entities = detect_gaps(posts, baseline, cfg)
    actions = evolve(gaps, new_entities, cfg)
    closed = resolve_gaps(baseline, cfg)
    attached = attach_gaps(posts, gaps, load_evolved())
    summary = {
        "enabled": True,
        "gap_count": len(gaps),
        "attached_posts": attached,
        "actions": actions.get("actions", []),
        "learned": actions.get("learned", []),
        "resolved": closed.get("resolved", []),
        "stale": closed.get("stale", []),
        "pending": closed.get("pending", []),
        "top_gaps": [{k: g[k] for k in ("topic", "gap_type", "blogger_count",
                                        "post_count", "reason", "samples")}
                     for g in gaps[:10]],
        "evolved": {k: v for k, v in load_evolved().items()
                    if v.get("status") in ("pending", "active", "resolved")},
    }
    storage.log(f"[evolution] 本轮：缺口 {len(gaps)} 个，新学实体词 "
                f"{len(actions.get('learned') or [])} 个，闭环解决 "
                f"{len(closed.get('resolved') or [])} 个，回写帖子 {attached} 条")
    return summary


def dashboard_state() -> dict:
    """看板用：缺口清单 + 进化状态。"""
    ev = load_evolved()
    live = {k: v for k, v in ev.items() if v.get("status") in ("pending", "active")}
    return {
        "active_keywords": sorted(live, key=lambda k: -live[k].get("bloggers", 0)),
        "resolved": sorted((k for k, v in ev.items() if v.get("status") == "resolved"),
                           key=lambda k: ev[k].get("resolved_at") or "", reverse=True)[:10],
        "stale": sorted((k for k, v in ev.items() if v.get("status") == "stale"))[:10],
        "dynamic_lexicon": load_dynamic_lexicon(),
    }
