"""采集器共享工具：UA、AI 相关性过滤、统一条目构造、时间解析、HTTP 抓取。

被 baseline.py / overseas_collector.py / ai_sources.py / policy_sources.py /
hotlist.py / events.py 共同引用（避免循环导入）。
"""
import re
from datetime import datetime, timezone

from .lexicon import extract_topics

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# AI 相关性过滤：标题/摘要命中任一话题才算领域舆情
# 范围 = AI 全领域 + 科技前沿热点（用户关注面含马斯克/特斯拉/自动驾驶等）
AI_HINT_WORDS = [
    "ai", "人工智能", "大模型", "aigc", "gpt", "claude", "gemini", "deepseek",
    "sora", "midjourney", "可灵", "即梦", "海螺", "vidu", "comfyui", "kimi",
    "豆包", "通义", "文心", "智谱", "星火", "元宝", "智能体", "文生视频",
    "图生视频", "ai绘画", "ai短剧", "数字人", "提示词", "ai工具", "ai视频",
    "ai编程", "cursor", "copilot", "suno", "manus", "quantization", "量化",
    "开源", "开放权重",
    # 科技前沿热点（AI 圈外延话题）
    "特斯拉", "tesla", "马斯克", "musk", "spacex", "自动驾驶", "机器人",
    "cybertruck", "optimus", "roadster",
]

# 营销活动物料标记（平台 campaign 官号内容，非自然舆情，聚合前剔除）
PROMO_MARKERS = ["全民制作人", "恰饭", "商单", "广告合作", "sponsored", "广告位招租"]

# 厂商客户案例/效果营销稿（OpenAI 官方新闻里大量 "How X cuts costs with ChatGPT"，
# 属市场物料而非舆情热点；聚合前剔除，避免稀释事件浓度）
CASE_STUDY_PATTERNS = [
    r"\bhow\s+\w+\s+(is|are)?\s*(turning?|cutting?|saving?|halving?|tripling?|"
    r"doubling?|reducing?|boosting?|running?|building?|bringing?|using?|leveraging?)\b",
    r"\b(cuts?|halves?|reduces?|slashes?)\s+[\w\s]{0,30}?\bcosts?\b",
    r"\b(cuts?|slashes?|reduces?)\s+[\w\s]{0,30}?\b(time|hours?)\s+by\b",
    r"\bsaves?\s+\w+\s+(hours?|time|money)\b",
    r"\btriples?\s+signups?\b", r"\bcase study\b", r"\bcustomer stor",
    r"\bbrings?\s+\w+\s+into\s+(chat|conversation)",
]
CASE_STUDY_RE = re.compile("|".join(CASE_STUDY_PATTERNS), re.IGNORECASE)


def _is_ai_related(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in AI_HINT_WORDS)


def _is_promo(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in PROMO_MARKERS)


def _is_case_study(text: str) -> bool:
    """厂商效果营销稿判定（How X cuts costs… / triples signups…）。"""
    return bool(CASE_STUDY_RE.search(text or ""))


# ---------------------------------------------------------------- HTTP 抓取
def _pick_encodings(r) -> list:
    """解码候选序列：响应头 charset → utf-8 → 探测 → gbk → latin-1。"""
    cands = []
    m = re.search(r"charset=([\w\-]+)", (r.headers or {}).get("Content-Type", ""), re.I)
    if m:
        cands.append(m.group(1))
    cands += ["utf-8", r.apparent_encoding, "gbk", "latin-1"]
    return [c for c in cands if c]


def http_get(url: str, timeout=15, headers=None, retries=1, backoff=2.0) -> str:
    """统一 GET：绕过系统代理 + 编码兜底 + 限流重试。

    三个必须这么写的原因（本机实测）：
      1) ``trust_env=False`` —— 本机存在系统代理，海外站走代理会 502/超时，
         国内站直连同样更快，故统一绕过；
      2) 解码兜底 —— requests 的 ``apparent_encoding``（chardet 探测）常把
         UTF-8 中文误判为 GBK/ISO-8859-1，标题会变成 "è¯ä»¶" 乱码；
      3) 重试 —— GDELT 等公共 API 限流为「1 请求 / 5 秒」，偶发 429 需退避重试，
         仍失败时抛异常由编排层降级，不拖垮整轮采集。
    """
    import requests
    import time as _t
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    last = None
    for i in range(retries + 1):
        try:
            s = requests.Session()
            s.trust_env = False
            r = s.get(url, headers=h, timeout=timeout)
            if r.status_code == 429:
                last = RuntimeError("rate-limited(429)")
                _t.sleep(backoff * (i + 1))
                continue
            r.raise_for_status()
            for enc in _pick_encodings(r):
                try:
                    return r.content.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return r.text
        except Exception as e:      # noqa: BLE001 —— 采集器一律降级不抛穿
            last = e
            if i < retries:
                _t.sleep(backoff * (i + 1))
    raise last


def http_get_json(url: str, timeout=20, headers=None, retries=1, backoff=2.0):
    """GET + JSON 解析（公共 API 统一入口）。"""
    import json
    return json.loads(http_get(url, timeout=timeout, headers=headers,
                               retries=retries, backoff=backoff))


def clean_text(s: str) -> str:
    """清洗标题/摘要：去 HTML 标签 + 折叠空白。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _parse_dt(s: str):
    """解析 ISO/日期串为 naive UTC datetime；纯日期串按当天中午 CST（避免午夜边界）。"""
    if not s:
        return None
    s = str(s).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T12:00:00+08:00"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _phase_of(times: list) -> str:
    """事件传播阶段（与技能 analyze.py 同款规则；单条事件按新鲜度判定）。"""
    if not times:
        return "未知"
    now = datetime.utcnow()
    if len(times) == 1:
        since_last = now - times[0]
        if since_last.total_seconds() <= 24 * 3600:
            return "活跃期"
        if since_last.total_seconds() <= 48 * 3600:
            return "回落期"
        return "消退期"
    span = times[-1] - times[0]
    since_last = now - times[-1]
    if span.total_seconds() < 6 * 3600:
        return "苗头期"
    if since_last.total_seconds() > 48 * 3600:
        return "消退期"
    if since_last.total_seconds() > 24 * 3600:
        return "回落期"
    return "发酵期" if len(times) >= 3 else "活跃期"


def _mk_item(platform, title, url, author="", published="", metrics=None, text=""):
    """构造 SKILL.md 第 4 步的统一条目结构。"""
    full = (title or "") + " " + (text or "")
    topics = extract_topics(full)
    tags = ["行业热点"]
    if topics:
        tags.append("话题:" + topics[0])
    return {
        "platform": platform,
        "title_or_text": (title or text or "")[:200],
        "_full_text": full[:400],
        "url": url,
        "author": author,
        "published_at": published,
        "metrics": metrics or {"views": 0, "likes": 0, "comments": 0, "reposts": 0,
                               "engagement_total": 0, "rank": None},
        "sentiment": "neutral",
        "sentiment_reason": "基线采集条目，情感待判定",
        "tags": tags,
        "topics": topics,
    }
