"""比对验证引擎：博主选题 vs 当前舆情基线。

四维评分（满分 100）：
  热度契合 heat  45 —— 选题命中的舆情事件有多热（报道覆盖广度 + 互动量归一）
  时效契合 timing 25 —— 事件传播阶段：发酵期最佳，消退期最差
  声量参照 volume 10 —— 博主该内容互动量 vs 事件平均互动量
  差异化   diff   20 —— 热点饱和度：同热点条目越多分越低（避免红海）

判定结论：
  🟢 踩中热点  总分≥75，选题与当前活跃舆情高度契合
  🟡 追热稳健  50-74 且事件处活跃/发酵期，方向正确热度中等
  🟡 追热偏早  50-74 且事件处苗头期，有先发空间
  🟡 追热偏晚  50-74 且事件处回落/消退期，热点在降温
  🔴 选题滞后  总分<50，与当前舆情关注点脱节
  🔵 独苗选题  领域内未命中任何热点事件——若多位博主同话题均未命中，
               判定为「采集缺口」（见 core/evolution），自动补采而非误判博主
  ⚪ 领域外内容  内容无任何 AI 领域话题（商业/财经/生活等），不参与舆情比对
"""
from . import storage
from .feedkit import _parse_dt
from .lexicon import extract_topics, bigrams, jaccard

import math  # noqa: E402
import re  # noqa: E402

PHASE_TIMING = {"发酵期": 1.0, "活跃期": 0.85, "苗头期": 0.70, "回落期": 0.40, "消退期": 0.10, "未知": 0.5}

MATCH_THRESHOLD = 0.25   # 选题-事件匹配度阈值


def _pov_events(baseline: dict) -> list:
    return baseline.get("pov_events") or []

# 宽泛概念话题：单独命中不足以建立「跟进」关联（还需共同实体词），
# 否则任何 AI编程/智能体 旧帖都会绑到最近的同类事件上（假关联）
BROAD_CONCEPTS = {
    "AIGC", "文生视频", "图生视频", "AI绘画", "AI短剧", "数字人", "智能体",
    "大模型", "多模态", "开源模型", "模型量化", "MCP协议", "工作流", "AI编程",
    "AI搜索", "AI硬件", "提示词", "AI音乐", "AI配音", "AI写作", "AI PPT",
    "AI视频生成", "AI变现", "AI教育",
}

# 显著性词提取的停用字（出现在 n-gram 里即视为通用词，不算「共同实体」）
_TERM_STOP_CHARS = set(
    "的了是在和与及对为以从到把被让使这那你我他她它们个之其或且但很太更最也又再"
    "于按根据通过关于对于由于随着为了所以但是如果虽然而且并且或者还是就是不是"
    "什么怎么谁哪多少高低上下中内外前后左右东西南北早晚年月日时分秒今明昨将"
    "被请来去坐看成打给比朝往沿顺经过当曾经正将要刚刚刚才平时往往常常有时候"
    "有时天方式方法问题情况方面工作内容数据信息技术应用产品服务平台公司团队"
    "项目功能效果质量性能能力体验用户开发者企业市场行业领域过程结果原因影响"
    "变化趋势未来目前现在最新首次正式即将已经正在继续完成实现提供包括包含涉"
    "及相关视频模型生成智能人工开源开放权重训练推理对话聊天语言请问大家自己"
    "其他另外怎样呢吗吧啊哦嗯哈呵")
# 英文停用词（显著性词提取用）
_TERM_STOP_EN = {
    "ai", "the", "and", "for", "with", "from", "that", "this", "you", "your",
    "our", "their", "its", "not", "are", "was", "were", "has", "have", "had",
    "will", "would", "can", "could", "should", "may", "might", "must", "about",
    "into", "over", "under", "again", "once", "here", "there", "when", "where",
    "why", "how", "what", "which", "who", "all", "any", "both", "each", "few",
    "more", "most", "other", "some", "such", "only", "own", "same", "than",
    "too", "very", "just", "also", "but", "because", "while", "during", "out",
    "off", "up", "down", "in", "on", "at", "by", "of", "to", "as", "is", "it",
    "no", "nor", "so", "if", "or", "an", "be", "been", "being", "do", "does",
    "did", "we", "they", "them", "then", "now", "new", "one", "two", "get",
    "got", "make", "made", "makes", "use", "used", "using", "via", "per", "vs",
    "etc", "com", "www", "http", "https", "html", "app", "apps",
}


def _salient_terms(text: str) -> set:
    """显著性实体词：中文 2-4 gram（滤停用字）+ 英文/数字词（len≥2）。

    不含词典别名（话题分已覆盖）——只保留「是不是同一件事」的判别词，
    如 封号/飞书/roadster/万星/bluesky/attie。
    """
    from .lexicon import LEXICON
    aliases = set()
    for als in LEXICON.values():
        for a in als:
            aliases.add(_norm_alias(a))
    terms = set()
    low = (text or "").lower()
    for m in re.finditer(r"[a-z0-9][a-z0-9.\-+]{1,}", low):
        w = m.group(0).strip(".-+")
        if len(w) >= 2 and w not in _TERM_STOP_EN:
            terms.add(w)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                g = run[i:i + n]
                if not (_TERM_STOP_CHARS & set(g)):
                    terms.add(g)
    return {t for t in terms if t not in aliases}


def _norm_alias(a: str) -> str:
    return re.sub(r"\s+", "", (a or "").lower())


def _time_sim(post_dt, ev_first, ev_last):
    """帖子与事件时间窗的贴近度 + 是否应被门控排除。

    窗口内 = 1.0；提前 ≤7 天 = 0.6（提前布局，只认本波次附近的先发）；
    提前 >7 天 → 门控（属于更早波次，本基线里没有就不配）；
    事件结束后按天衰减，>30 天 → 门控（热点早已过气，旧闻不配新帖）。
    返回 (sim, gated)。
    """
    if not post_dt or not ev_first:
        return 0.5, False
    if ev_last and post_dt > ev_last:
        gap_days = (post_dt - ev_last).total_seconds() / 86400
        if gap_days > 30:
            return 0.0, True
        return max(0.2, 1.0 - gap_days * 0.04), False
    if post_dt < ev_first:
        gap_days = (ev_first - post_dt).total_seconds() / 86400
        if gap_days > 7:
            return 0.0, True
        return 0.6, False
    return 1.0, False


def _match_events(post_topics, post_text, post_dt, baseline):
    """返回 [(match_score, event_meta)] 按匹配度降序。

    匹配度 = 0.5 * 话题分 + 0.3 * 实体词重合 + 0.2 * 时间贴近度。

    关联规则（防假关联）：
    1. 话题必须有交集；其中**宽泛概念**（智能体/大模型/AI编程/工作流…）
       单独不算数——还需与事件标题有实质共同实体词（term_cov≥0.15 或
       共享词≥2），否则任何同类旧帖都会绑到最近的事件上
    2. 产品话题（豆包/Claude/REA/可灵…）命中即算相关（同产品圈动态），
       实体词重合仅影响排序
    3. 时间门控：早于事件首发 >7 天、或晚于事件末次 >30 天 → 不绑定
       （不属于这一波新闻）
    """
    from .lexicon import PRODUCT_LEXICON
    post_fp = bigrams(post_text)
    post_set = set(post_topics)
    post_terms = _salient_terms(post_text)
    out = []
    for ev in _pov_events(baseline):
        if ev.get("is_signal"):
            continue        # 论文/开源层不参与验证判据，详见 source_registry.SIGNAL_CLASSES
        ev_topics = ev.get("topics") or []
        overlap = post_set & set(ev_topics)
        if not overlap:
            continue
        spec = 1.0 if any(t in PRODUCT_LEXICON for t in overlap) else 0.8
        topic_sim = spec * len(overlap) / len(ev_topics)
        ev_text = " ".join(ev.get("item_titles") or [ev.get("title") or ""])
        text_sim = jaccard(post_fp, bigrams(ev_text))
        ev_terms = _salient_terms(ev_text)
        shared = post_terms & ev_terms
        term_cov = len(shared) / max(1, min(len(post_terms), len(ev_terms)))
        t_sim, gated = _time_sim(post_dt, _parse_dt(ev.get("first_seen")),
                                 _parse_dt(ev.get("last_seen")))
        if gated:
            continue
        specific = [t for t in overlap if t not in BROAD_CONCEPTS]
        if not specific and term_cov < 0.15 and len(shared) < 2:
            continue        # 只有宽泛话题且无共同实体 → 视为无关，不绑定
        score = 0.5 * topic_sim + 0.3 * term_cov + 0.2 * t_sim
        if score < MATCH_THRESHOLD:
            continue
        meta = {
            "id": ev.get("id"), "topic": ev.get("topic"), "title": ev.get("title") or "",
            "phase": ev.get("phase") or "未知", "count": ev.get("count") or 0,
            "topics": ev_topics, "overlap_topics": sorted(overlap),
            "avg_eng": ev.get("avg_eng") or 0, "max_eng": ev.get("max_eng") or 0,
            "hotspot_ratio": ev.get("hotspot_ratio") or 0,
            "sentiment": ev.get("sentiment") or {},
            "platforms": ev.get("platforms") or [],
            "first_seen": ev.get("first_seen") or "",
            "last_seen": ev.get("last_seen") or "",
        }
        out.append((round(score, 3), meta))
    out.sort(key=lambda x: -x[0])
    return out


def verify_post(post: dict, baseline: dict, weights: dict | None = None) -> dict:
    w = weights or {"heat": 40, "timing": 25, "volume": 15, "diff": 20}
    post_text = (post.get("title") or "") + " " + (post.get("text") or "")
    m = post.get("metrics") or {}
    post_eng = int(m.get("likes", 0) + m.get("comments", 0) + m.get("shares", 0))

    if not baseline or not _pov_events(baseline):
        return {"verdict": "数据不足", "emoji": "⚪", "total": None,
                "reason": "尚无舆情基线，无法比对", "advice": "请先执行基线采集"}

    post_dt = _parse_dt(post.get("published_at"))
    # 话题缓存可能过期：词典会不断扩充（REA/Claude/马斯克汽车等后加入），
    # 采集时落库的 topics 不含新词 → 验证时用当前词典重新抽取并与缓存取并集，
    # 避免「帖子里明写了 #REA 却因缓存旧值只拿到宽泛话题 AI编程」而漏配
    post_topics = sorted(set(post.get("topics") or []) | set(extract_topics(post_text)))
    if not post_topics:
        # 领域外内容：财经/商业/生活类选题，本就不该参与 AI 舆情比对。
        # 单独归类，避免把「博主没追 AI 热点」误统计成独苗、稀释追热率。
        return {
            "verdict": "领域外内容", "emoji": "⚪", "total": None,
            "match_score": None, "matched_event": None,
            "post_topics": [],
            "reason": "内容无任何 AI 领域话题命中，不属于舆情比对范围",
            "advice": "领域外选题（商业/财经/生活等），已从追热统计中剔除；"
                      "若想纳入比对，内容需体现 AI 工具/AIGC 领域话题。",
        }
    matches = _match_events(post_topics, post_text, post_dt, baseline)
    best_score, best_ev = matches[0] if matches else (0.0, None)

    if best_ev is None or best_score < MATCH_THRESHOLD:
        return {
            "verdict": "独苗选题", "emoji": "🔵", "total": None,
            "match_score": best_score, "matched_event": None,
            "post_topics": post_topics,
            "reason": "未命中当前舆情任何热点事件",
            "advice": "偏离当前舆情关注点。若是刻意做差异化可继续观察数据反馈；"
                      "若想追热，可参考看板右侧「当前舆情关注点」清单。",
        }

    import math
    # 热度契合：覆盖广度（主信号，报道条数相对值）+ 互动量（相对最热事件归一）
    # 事件本身无互动数据时取中性 0.5（搜索源条目不带互动量，不应被其他源的互动量相对归零）
    events = _pov_events(baseline)
    max_avg = max((e.get("avg_eng") or 0) for e in events) if events else 0
    max_count = max((e.get("count") or 0) for e in events) if events else 0
    coverage = min(1.0, (best_ev["count"] or 1) / (max_count or 1))
    if best_ev["avg_eng"] > 0 and max_avg > 0:
        eng_rel = math.log10(best_ev["avg_eng"] + 1) / math.log10(max_avg + 1)
    else:
        eng_rel = 0.5
    heat = min(1.0, 0.65 * coverage + 0.35 * eng_rel)
    # 时效契合：事件阶段（发酵期=峰值最佳）
    timing = PHASE_TIMING.get(best_ev["phase"], 0.5)
    # 差异化：饱和度 = 该事件条目数 / 最大事件条目数
    saturation = min(1.0, (best_ev["count"] or 1) / (max_count or 1))
    diff = 1.0 - saturation * 0.6   # 最热事件也有 0.4 保底

    # 声量参照：博主互动 / 事件均互动。事件无互动量数据时该维度无法比较，
    # 剔除该维度并按剩余维度权重重新归一（避免无数据被按中性值稀释）。
    if best_ev["avg_eng"] > 0:
        volume = min(1.0, post_eng / (best_ev["avg_eng"] + 1))
        w_vol = w["volume"]
    else:
        volume, w_vol = None, 0
    w_sum = w["heat"] + w["timing"] + w["diff"] + w_vol
    raw = (heat * w["heat"] + timing * w["timing"] + diff * w["diff"]
           + (volume * w_vol if volume is not None else 0))
    total = round(raw / w_sum * 100)
    phase = best_ev["phase"]
    cooling = phase in ("回落期", "消退期")   # 热点在降温

    if cooling:
        # 降温期话题：分数高说明吃到了尾量，分数低说明选题已过气
        if total >= 75:
            verdict, emoji = "踩中热点", "🟢"
            advice = (f"选题命中「{best_ev['title'][:24]}」相关热点（{phase}），"
                      f"虽热点已开始降温，但内容数据表现强劲，成功吃到尾量。")
        elif total >= 50:
            verdict, emoji = "追热偏晚", "🟡"
            advice = (f"热点「{best_ev['title'][:24]}」已进入{phase}，热度在降温，"
                      f"需靠差异化角度或更深解读取胜，跟进性价比已下降。")
        else:
            verdict, emoji = "选题滞后", "🔴"
            advice = (f"选题相关舆情已消退（{phase}），与当前关注点脱节，"
                      f"建议转向看板右侧当前关注点清单中的活跃事件。")
    else:
        # 活跃/发酵/苗头期话题：热度未衰，低分主因是覆盖薄或声量小
        if total >= 75:
            verdict, emoji = "踩中热点", "🟢"
            advice = (f"选题命中「{best_ev['title'][:24]}」相关热点（{phase}），"
                      f"与当前舆情关注点高度契合，追热时机好。")
        elif total >= 50:
            if phase == "苗头期":
                verdict, emoji = "追热偏早", "🟡"
                advice = (f"热点「{best_ev['title'][:24]}」尚处苗头期，"
                          f"现在入场有先发优势，可提前布局同选题。")
            else:
                verdict, emoji = "追热稳健", "🟡"
                advice = (f"选题命中活跃热点「{best_ev['title'][:24]}」（{phase}），"
                          f"方向正确；热度或声量中等，靠选题角度与发布节奏仍有放大空间。")
        else:
            verdict, emoji = "追热偏早", "🟡"
            advice = (f"选题方向「{best_ev['title'][:24]}」仍在{phase}，但当前舆情覆盖尚薄，"
                      f"属于早期机会——先发者受益，也需承担热点不发酵的风险。")

    return {
        "verdict": verdict, "emoji": emoji, "total": total,
        "match_score": best_score,
        "matched_event": best_ev,
        "post_topics": post_topics,
        "scores": {"heat": round(heat * w["heat"], 1), "timing": round(timing * w["timing"], 1),
                   "volume": (round(volume * w["volume"], 1) if volume is not None else None),
                   "diff": round(diff * w["diff"], 1)},
        "reason": f"匹配事件「{best_ev['title'][:30]}」（匹配度 {best_score:.2f}，{best_ev['phase']}）",
        "advice": advice,
    }


def verify_all(posts: list, baseline: dict, cfg: dict) -> list:
    weights = (cfg.get("verify") or {}).get("weights")
    results = []
    for p in posts:
        v = verify_post(p, baseline, weights)
        p2 = dict(p)
        p2["verification"] = v
        results.append(p2)
    return results


def opinion_focus(baseline: dict, top_n: int = 10) -> list:
    """当前舆情关注点清单：按「覆盖广度 + 互动量 + 阶段新鲜度」综合排序，供选题参考。"""
    focus = []
    for ev in _pov_events(baseline):
        if ev.get("is_signal"):
            continue        # 论文/开源属超前信号，不计入「当前舆情关注点」
        phase_bonus = {"发酵期": 1.0, "活跃期": 0.85, "苗头期": 0.7, "回落期": 0.3, "消退期": 0.1}.get(
            ev.get("phase") or "未知", 0.5)
        attn = ((ev.get("count") or 0) * 2
                + math.log10((ev.get("avg_eng") or 0) + 1)
                + phase_bonus * 2)
        focus.append({
            "id": ev.get("id"), "topic": ev.get("topic"),
            "title": ev.get("title") or "",
            "phase": ev.get("phase") or "未知",
            "topics": ev.get("topics") or [],
            "count": ev.get("count") or 0,
            "avg_eng": ev.get("avg_eng") or 0, "max_eng": ev.get("max_eng") or 0,
            "hotspot_ratio": ev.get("hotspot_ratio") or 0,
            "attention": round(attn, 2),
            "sentiment": ev.get("sentiment") or {},
            "platforms": ev.get("platforms") or [],
        })
    focus.sort(key=lambda f: -f["attention"])
    return focus[:top_n]


def opinion_signals(baseline: dict, top_n: int = 12) -> list:
    """前沿信号清单：论文 / 开源项目。

    与 ``opinion_focus`` 的分工：
        focus   已成形舆情（有报道覆盖、有讨论量）——用于判「追热 / 滞后」
        signals 尚未成形的超前信号（论文/开源首发）——用于判「提前卡位」

    论文与开源项目往往是下一波热点的源头：一个项目先在 GitHub Trending 登顶，
    数天后才被媒体刷屏。把它们单列而非混入主事件库，既保留了预警价值，
    又避免博主因为「没追一篇当下无人讨论的论文」而被误判为滞后。
    """
    out = []
    for ev in _pov_events(baseline):
        if not ev.get("is_signal"):
            continue
        out.append({
            "id": ev.get("id"), "topic": ev.get("topic"),
            "title": ev.get("title") or "",
            "classes": ev.get("classes") or [],
            "topics": ev.get("topics") or [],
            "platforms": ev.get("platforms") or [],
            "count": ev.get("count") or 0,
            "max_eng": ev.get("max_eng") or 0,
            "first_seen": ev.get("first_seen") or "",
        })
    out.sort(key=lambda s: (-(s["max_eng"] or 0), s["first_seen"] or ""))
    return out[:top_n]


def blogger_hot_topics(verified_posts: list, top_n: int = 10,
                       recent_days: int = 30) -> list:
    """博主圈热议话题（用户的真实关注面）：从已监控博主的内容聚合。

    与「舆情关注点」（外部基线）互补——这个清单回答「我关注的人最近在
    集中做什么选题」。统计近 recent_days 天帖子：话题频次、涉及博主数、
    追热命中率（命中舆情事件的占比）与代表内容。
    """
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=recent_days)
    by_topic = {}
    for p in verified_posts:
        pub = _parse_dt(p.get("published_at"))
        if pub and pub < cutoff:
            continue
        v = p.get("verification") or {}
        riding = v.get("verdict") in ("踩中热点", "追热稳健", "追热偏早", "追热偏晚")
        for t in (p.get("topics") or []):
            d = by_topic.setdefault(t, {"topic": t, "posts": 0, "bloggers": set(),
                                        "riding": 0, "scores": [], "latest": "",
                                        "sample": ""})
            d["posts"] += 1
            if p.get("blogger"):
                d["bloggers"].add(p["blogger"])
            if riding:
                d["riding"] += 1
            if v.get("total") is not None:
                d["scores"].append(v["total"])
            pub_iso = p.get("published_at") or ""
            if pub_iso > d["latest"]:
                d["latest"] = pub_iso
                d["sample"] = (p.get("title") or "")[:60]
    out = []
    for d in by_topic.values():
        if d["posts"] < 2:
            continue
        out.append({
            "topic": d["topic"], "posts": d["posts"],
            "bloggers": len(d["bloggers"]),
            "riding": d["riding"],
            "riding_ratio": round(d["riding"] / d["posts"], 2),
            "avg_score": round(sum(d["scores"]) / len(d["scores"])) if d["scores"] else None,
            "latest": d["latest"], "sample": d["sample"],
        })
    out.sort(key=lambda x: (-x["posts"], -x["bloggers"]))
    return out[:top_n]


def blogger_profiles(verified_posts: list) -> list:
    """博主选题风格画像。"""
    by_blogger = {}
    for p in verified_posts:
        v = p.get("verification") or {}
        name = p.get("blogger")
        b = by_blogger.setdefault(name, {"blogger": name, "platform": p.get("platform"),
                                         "total": 0, "counts": {}, "scores": []})
        b["total"] += 1
        b["counts"][v.get("verdict", "数据不足")] = b["counts"].get(v.get("verdict", "数据不足"), 0) + 1
        if v.get("total") is not None:
            b["scores"].append(v["total"])
    out = []
    for b in by_blogger.values():
        counts = b["counts"]
        riding = (counts.get("踩中热点", 0) + counts.get("追热稳健", 0)
                  + counts.get("追热偏早", 0) + counts.get("追热偏晚", 0))
        unique = counts.get("独苗选题", 0)
        off = counts.get("领域外内容", 0)
        denom = b["total"] - off          # 领域外内容不参与风格判定
        if denom <= 0:
            style = "非AI领域"
        elif riding / denom >= 0.6:
            style = "追热型"
        elif unique / denom >= 0.5:
            style = "独苗型"
        else:
            style = "稳健型"
        avg = round(sum(b["scores"]) / len(b["scores"])) if b["scores"] else None
        out.append({**b, "avg_score": avg, "style": style, "counts": counts,
                    "off_domain": off})
    out.sort(key=lambda x: -x["total"])
    return out
