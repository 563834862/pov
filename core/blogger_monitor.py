"""博主内容监控：检测配置清单中博主的新内容。

模式：
  simulated —— 示例数据（明确标记），用于跑通验证流程；
               由「固定选题池 + 基于当前舆情热点的追热样例」组成，
               覆盖 踩中热点/滞后/独苗 三类选题行为，供验证引擎演示。
  weibo_uid —— 微博用户时间线采集（container API，需博主的 UID）
  bilibili  —— B站 UP 主空间动态采集（最佳努力，失败自动降级）

新内容判定：URL 不在 data/posts/posts.json 已见集合中 → 新内容。
"""
import hashlib
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import storage
from .lexicon import extract_topics

# ---------------------------------------------------------------- 固定选题池
# 每个示例博主的固定内容池：覆盖 追热 / 长青 / 小众独特 三类选题风格
FIXED_POOL = {
    "AI每日观察（示例）": [
        {"title": "DeepSeek 又更新了！这次改了什么，3分钟说明白",
         "text": "DeepSeek 新版本发布，长文本能力增强，我们实测了代码和写作场景。",
         "age_h": 5, "likes": 3200, "comments": 480, "shares": 210},
        {"title": "我用 AI 给老家果园做了条宣传片，全村都来问怎么做的",
         "text": "国庆回家用即梦+剪映给果园做了条宣传片，零基础全程手机操作。",
         "age_h": 26, "likes": 8600, "comments": 940, "shares": 520},
        {"title": "回顾：Sora 刚发布那天，整个 AI 圈炸了",
         "text": "去年 Sora 发布时的震撼还历历在目，回头看文生视频这一年。",
         "age_h": 200, "likes": 1500, "comments": 120, "shares": 60},
    ],
    "工具喵AIGC（示例）": [
        {"title": "ChatGPT 最新功能实测：这个隐藏技巧太香了",
         "text": "ChatGPT 更新后新增的能力，手把手教你用起来。",
         "age_h": 8, "likes": 5400, "comments": 620, "shares": 340},
        {"title": "ComfyUI 工作流分享：一键生成电商主图",
         "text": "分享一个跑通的 ComfyUI 电商主图工作流，附参数。",
         "age_h": 30, "likes": 2100, "comments": 260, "shares": 180},
        {"title": "AI 配音踩坑指南：这些工具别乱买",
         "text": "测了 6 款 AI 配音工具，告诉大家哪些值得买哪些是坑。",
         "age_h": 300, "likes": 980, "comments": 88, "shares": 40},
    ],
    "AIGC情报站（示例）": [
        {"title": "本周 AI 圈大事：多个大模型集中更新",
         "text": "盘点半周内大模型领域的重要更新与发布。",
         "age_h": 3, "likes": 1800, "comments": 150, "shares": 95},
        {"title": "可灵新版本实测：运镜和一致性提升明显",
         "text": "可灵视频生成模型新版实测，重点测试了人物一致性。",
         "age_h": 20, "likes": 4300, "comments": 380, "shares": 260},
        {"title": "数字人直播还能玩吗？调研了 20 个商家后的结论",
         "text": "数字人直播带货调研，含平台规则与转化数据。",
         "age_h": 400, "likes": 2600, "comments": 310, "shares": 120},
    ],
    "短剧实验室（示例）": [
        {"title": "AI 短剧《山那边的学校》分镜全流程复盘",
         "text": "从剧本到成片，AI 短剧制作的完整工作流拆解。",
         "age_h": 12, "likes": 9200, "comments": 1100, "shares": 680},
        {"title": "一个人做 AI 短片：剧本、配音、剪辑全解析",
         "text": "单人 AI 短片制作流程，含工具链和成本核算。",
         "age_h": 40, "likes": 3600, "comments": 420, "shares": 300},
        {"title": "用 AI 做农村房产短视频，中介同行都在学",
         "text": "房屋中介用 AI 生成房源短视频的实操方法。",
         "age_h": 350, "likes": 1200, "comments": 90, "shares": 55},
    ],
}

# 追热样例模板：基于当前舆情热点生成（明确标记示例）
TREND_TEMPLATES = [
    ("{topic} 突然爆火，我们扒了扒背后的技术",
     "{topic} 相关讨论量激增，这篇文章讲清楚它是什么、能干什么。"),
    ("全网都在聊 {topic}，普通人怎么用上？",
     "{topic} 最近刷屏了，整理一份零基础上手指南。"),
    ("{topic} 翻车还是真香？实测之后说说真相",
     "针对 {topic} 的热议，我们做了实际测试，结论可能和你想的不一样。"),
]


def _mk_post(blogger_cfg, title, text, published_at, metrics, simulated=True):
    now = datetime.now(timezone.utc)
    pid = hashlib.md5((blogger_cfg["name"] + title).encode()).hexdigest()[:10]
    return {
        "id": pid,
        "blogger": blogger_cfg["name"],
        "platform": blogger_cfg.get("platform", ""),
        "title": title,
        "text": text,
        "url": blogger_cfg.get("url", "") + f"#{pid}",
        "published_at": published_at,
        "metrics": metrics,
        "topics": extract_topics(title + " " + text),
        "simulated": simulated,
        "first_seen_at": now.isoformat(timespec="seconds"),
    }


def _trend_posts(blogger_cfg, baseline, now):
    """基于当前舆情热点生成追热样例（最多 2 条/博主）。

    只从「仍在发酵/活跃」的事件中选话题（消退期不追），
    按博主轮换话题分布，模板用稳定哈希选择。
    """
    posts = []
    events = (baseline or {}).get("pov_events") or []
    fresh = [e for e in events if e.get("phase") in ("发酵期", "活跃期", "苗头期")]
    fresh.sort(key=lambda e: -((e.get("count") or 0) * {"发酵期": 1.0, "活跃期": 0.85, "苗头期": 0.7}.get(e.get("phase"), 0.5)))
    candidates = [e.get("topic") for e in fresh if e.get("topic")]
    if not candidates:
        return posts
    # 按博主稳定轮换：不同博主追不同话题组合
    seed = int(hashlib.md5(blogger_cfg["name"].encode()).hexdigest()[:8], 16)
    rotated = candidates[seed % len(candidates):] + candidates[:seed % len(candidates)]
    for i, topic in enumerate(rotated[:2]):
        tpl = TREND_TEMPLATES[(seed + i) % len(TREND_TEMPLATES)]
        title = tpl[0].format(topic=topic)
        text = tpl[1].format(topic=topic)
        hours_ago = 2 + i * 5 + (seed % 3)
        posts.append(_mk_post(
            blogger_cfg, title, text,
            (now - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
            {"likes": 1500 + 800 * i + (seed % 2000),
             "comments": 180 + 60 * i, "shares": 90 + 40 * i},
        ))
    return posts


def _simulated_check(blogger_cfg, baseline, existing_ids):
    now = datetime.now(timezone.utc)
    out = []
    pool = FIXED_POOL.get(blogger_cfg["name"], [])
    for item in pool:
        post = _mk_post(
            blogger_cfg, item["title"], item["text"],
            (now - timedelta(hours=item["age_h"])).isoformat(timespec="seconds"),
            {"likes": item["likes"], "comments": item["comments"], "shares": item["shares"]},
        )
        if post["id"] not in existing_ids:
            out.append(post)
    for post in _trend_posts(blogger_cfg, baseline, now):
        if post["id"] not in existing_ids:
            out.append(post)
    return out


def _weibo_check(blogger_cfg, existing_ids):
    """微博用户时间线（最佳努力，失败返回空并记录）。"""
    uid = blogger_cfg.get("uid")
    if not uid:
        return []
    try:
        from .baseline import _fetch
        url = ("https://m.weibo.cn/api/container/getIndex?"
               f"containerid=107603{uid}&type=uid")
        data = __import__("json").loads(_fetch(url))
        out = []
        for card in ((data.get("data") or {}).get("cards") or []):
            mblog = card.get("mblog")
            if not mblog:
                continue
            text = re.sub(r"<[^>]+>", "", mblog.get("text", ""))[:200]
            post = _mk_post(
                blogger_cfg, text[:40] or "微博动态", text,
                mblog.get("created_at", ""),
                {"likes": mblog.get("attitudes_count", 0),
                 "comments": mblog.get("comments_count", 0),
                 "reposts": mblog.get("reposts_count", 0)},
                simulated=False,
            )
            if post["id"] not in existing_ids:
                out.append(post)
        return out
    except Exception as e:
        storage.log(f"微博采集失败 {blogger_cfg['name']}: {e}")
        return []


def check_bloggers(cfg: dict, baseline: dict | None = None) -> tuple:
    """检查所有博主的更新，返回 (新内容列表, 各博主状态)。"""
    existing = storage.load_posts()
    existing_ids = {p.get("id") for p in existing}
    bloggers = cfg.get("bloggers") or []
    new_posts, status = [], {}

    # 抖音博主：走浏览器桥接批量采集（复用 Chrome 登录态）
    dy_bloggers = [b for b in bloggers if b.get("mode") == "douyin_bridge"]
    if dy_bloggers:
        from . import douyin_bridge
        dy_posts, dy_status = douyin_bridge.collect_all(dy_bloggers, existing_ids)
        new_posts.extend(dy_posts)
        status.update(dy_status)

    for b in bloggers:
        if b.get("mode") == "douyin_bridge":
            continue  # 已批量处理
        mode = b.get("mode", "simulated")
        if mode == "simulated":
            found = _simulated_check(b, baseline, existing_ids)
            status[b["name"]] = f"simulated:{len(found)}"
        elif mode == "weibo_uid":
            found = _weibo_check(b, existing_ids)
            status[b["name"]] = f"weibo:{len(found)}"
        else:
            found, status[b["name"]] = [], f"unsupported:{mode}"
        new_posts.extend(found)

    if new_posts:
        storage.save_posts(existing + new_posts)
        storage.log(f"博主监控: 新增 {len(new_posts)} 条内容")
    return new_posts, status
