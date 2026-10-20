"""舆情基线采集：多源适配器 + 分层编排 + 降级 + 复用 public-opinion-monitor 技能分析脚本。

数据源分五层登记在 ``core/source_registry.py``（依据《舆情与AI新闻采集来源总清单》），
优先级遵循「官方 API > 原生 RSS > RSSHub > 爬虫」：

    政策层     gov_policy / federal_register / gdelt
    AI一手     arxiv / hf_papers / github_trending
    媒体社区    techcrunch_ai / theverge_ai / arstechnica / mittr / openai_news /
              venturebeat_ai / hackernews / producthunt / qbitai / infoq
    国内舆情   search_engine / kr36 / baidu_news / toutiao / bilibili_rank
    热度榜     weibo_hot / baidu_hot / toutiao_hot
    桥接喂入   x_search / youtube_search

采集结果统一为 SKILL.md 第 4 步的 JSON 结构，交给技能 scripts/analyze.py
做去重/聚类/健康分/事件阶段分析，产出基线快照。

入库前先做去重与过滤（顺序：营销物料 → 重复舆情 → 英文标题翻译）：
  dedup.dedup_items 判定「是不是同一件事」，完全重复的条目不再入库，
  来源并入保留条目的 sources，避免重复计数抬高热度。
"""
import json
import re
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import storage
from .events import build_news_events
from .feedkit import UA, _is_ai_related, _is_promo, _mk_item
from .lexicon import extract_topics
from .source_registry import build_collectors, summary as source_summary, validate as validate_sources

SKILL_SCRIPTS = Path(r"C:\Users\Administrator.SY-202311081432\.workbuddy\skills\public-opinion-monitor\scripts")
ANALYZE_PY = SKILL_SCRIPTS / "analyze.py"


# ---------------------------------------------------------------- 采集适配器
def collect_search_engine(keywords: list, top_n: int) -> tuple:
    """读取 Agent/用户预先放入 data/inputs/ 的搜索结果 JSON。

    文件格式: [{"title":..., "url":..., "snippet":..., "date":..., "source":...}, ...]
    同一来源页面的多条信息加 #sN 片段区分条目（来源链接仍指向真实页面）。
    """
    items, files = [], sorted(storage.INPUTS.glob("search-*.json"), reverse=True)
    used = files[:3]  # 最近 3 个文件，避免重复堆叠
    for fp in used:
        data = storage.read_json(fp, default=[]) or []
        for i, r in enumerate(data):
            title = r.get("title") or ""
            if not _is_ai_related(title + " " + r.get("snippet", "")):
                continue
            base_url = (r.get("url") or "").split("#")[0]
            items.append(_mk_item(
                platform="search",
                title=title,
                url=f"{base_url}#s{i}" if base_url else "",
                author=r.get("source") or "",
                published=r.get("date") or "",
                text=r.get("snippet") or "",
            ))
    status = "ok" if items else ("no-input" if not used else "filtered-empty")
    return items[:top_n * 4], status


def _fetch(url: str, timeout=12) -> str:
    """抓取页面文本，按 响应头charset → utf-8 → 探测 的顺序解码。

    requests 的 apparent_encoding（chardet 探测）常把 UTF-8 中文误判为
    GBK/ISO-8859-1，中文标题会变成 "è¯ä»¶" 这类乱码；
    响应头 charset 与 UTF-8 优先可避免该问题。
    """
    import requests
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    cands = []
    m = re.search(r"charset=([\w\-]+)", (r.headers or {}).get("Content-Type", ""), re.I)
    if m:
        cands.append(m.group(1))
    cands += ["utf-8", r.apparent_encoding, "gbk", "latin-1"]
    for enc in cands:
        if not enc:
            continue
        try:
            return r.content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return r.text


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    """去掉标题里残留的 HTML 标签（搜索结果常带 <em> 高亮）。"""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", s or "")).strip()


def _unescape(s: str) -> str:
    """解析 JSON 字符串转义（\\uXXXX / \\n …）。

    注意：不能用 s.encode().decode("unicode_escape")——那会把 UTF-8 字节
    按 latin-1 解码，中文全变 "è¯ä»¶" 这类乱码。
    """
    try:
        return json.loads('"' + (s or "") + '"')
    except (ValueError, TypeError):
        return s or ""


def collect_kr36(keywords: list, top_n: int) -> tuple:
    """36氪搜索页解析（直连可用）。"""
    items = []
    try:
        html = _fetch(f"https://36kr.com/search/articles/{urllib.parse.quote(keywords[0])}")
        # 搜索结果在 SSR 的 <script> JSON 或文章卡片中
        for m in re.finditer(r'"title":"([^"]{8,120})"[^}]*?"route":"([^"]+)"', html):
            title = _strip_tags(_unescape(m.group(1)))
            if not _is_ai_related(title):
                continue
            route = m.group(1) and m.group(2)
            items.append(_mk_item("kr36", title, f"https://36kr.com{route}" if route else "https://36kr.com"))
        # 兜底：HTML 卡片
        if not items:
            for m in re.finditer(r'<a[^>]+href="(/p/[^"]+)"[^>]*>([^<]{8,120})</a>', html):
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if _is_ai_related(title):
                    items.append(_mk_item("kr36", title, "https://36kr.com" + m.group(1)))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    return items[:top_n * 3], ("ok" if items else "empty")


def collect_baidu_news(keywords: list, top_n: int) -> tuple:
    """百度新闻搜索解析。"""
    items = []
    try:
        html = _fetch(f"https://news.baidu.com/ns?word={urllib.parse.quote(keywords[0])}"
                      f"&tn=news&from=news&cl=2&rn=20&ct=1")
        for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*?_blank">([^<]{8,120})</a>', html):
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if _is_ai_related(title):
                items.append(_mk_item("baidu_news", title, m.group(1)))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    return items[:top_n * 3], ("ok" if items else "empty")


def collect_toutiao(keywords: list, top_n: int) -> tuple:
    """今日头条搜索解析。"""
    items = []
    try:
        html = _fetch(f"https://so.toutiao.com/search?keyword={urllib.parse.quote(keywords[0])}"
                      f"&pd=information")
        for m in re.finditer(r'"title":"([^"]{8,120})".*?"url":"(https?://[^"]+)"', html):
            title = _strip_tags(_unescape(m.group(1)))
            if _is_ai_related(title):
                items.append(_mk_item("toutiao", title, m.group(2)))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    return items[:top_n * 3], ("ok" if items else "empty")


def collect_bilibili_rank(keywords: list, top_n: int) -> tuple:
    """B站全站热榜 JSON，按标题过滤 AI 相关。"""
    items = []
    try:
        import requests
        from datetime import datetime, timezone
        r = requests.get("https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all",
                         headers={"User-Agent": UA, "Referer": "https://www.bilibili.com"},
                         timeout=12)
        data = r.json()
        for v in (data.get("data") or {}).get("list") or []:
            title = v.get("title") or ""
            if not _is_ai_related(title):
                continue
            stat = v.get("stat") or {}
            pub = v.get("pubdate")
            published = (datetime.fromtimestamp(int(pub), tz=timezone.utc).isoformat(timespec="seconds")
                         if pub else "")
            items.append(_mk_item(
                platform="bilibili",
                title=title,
                url=f"https://www.bilibili.com/video/{v.get('bvid')}",
                author=v.get("owner", {}).get("name") or "",
                published=published,
                metrics={"views": stat.get("view", 0), "likes": stat.get("like", 0),
                         "comments": stat.get("reply", 0), "reposts": stat.get("share", 0),
                         "engagement_total": stat.get("like", 0) + stat.get("reply", 0),
                         "rank": v.get("idx") or None},
            ))
    except Exception as e:
        return [], f"fail:{type(e).__name__}"
    return items[:top_n * 3], ("ok" if items else "empty")


# 国内源（本文件实现）；其余层由 source_registry 合并
DOMESTIC_COLLECTORS = {
    "search_engine": collect_search_engine,
    "kr36": collect_kr36,
    "baidu_news": collect_baidu_news,
    "toutiao": collect_toutiao,
    "bilibili_rank": collect_bilibili_rank,
}

COLLECTORS = build_collectors(DOMESTIC_COLLECTORS)

# 需要额外入参的源（从 config.yaml 的 sources_extra 读取）
EXTRA_KWARGS = {
    "arxiv": lambda x: {"queries": x.get("arxiv_queries")},
    "federal_register": lambda x: {"terms": x.get("fedreg_terms")},
    "gov_policy": lambda x: {"terms": x.get("gov_terms")},
    "gdelt": lambda x: {"query": x.get("gdelt_query")},
    "github_trending": lambda x: {"token": x.get("github_token") or ""},
}


def _call(name: str, keywords: list, top_n: int, extra: dict):
    """调用单个采集器，自动带入其额外入参。"""
    fn = COLLECTORS.get(name)
    if fn is None:
        return [], "unknown-source"
    kw = EXTRA_KWARGS.get(name, lambda x: {})(extra or {})
    return fn(keywords, top_n, **kw)


def collect_all(cfg: dict) -> tuple:
    """并行采集所有配置的源，返回 (items, status_dict)。

    源清单 = ``baseline.sources`` + ``sources_extra.enabled``；
    启动前先做配置校验，把「未登记源 / 已关停源 / 缺自建 RSSHub」以告警形式
    落日志，避免源拼错后静默失效（表现为采集量莫名下降却无任何报错）。
    """
    bcfg = cfg.get("baseline", {})
    extra = cfg.get("sources_extra", {})
    keywords = cfg.get("keywords") or ["AI工具", "AIGC"]
    top_n = int(bcfg.get("top_n", 10))
    sources = list(bcfg.get("sources") or []) + list(extra.get("enabled") or [])
    if not sources:
        sources = list(COLLECTORS.keys())

    for w in validate_sources(sources):
        storage.log(f"采集源配置告警：{w}")

    results = {}
    uniq = list(dict.fromkeys(sources))
    with ThreadPoolExecutor(max_workers=min(16, len(uniq)) or 1) as pool:
        futs = {name: pool.submit(_call, name, keywords, top_n, extra)
                for name in uniq}
        for name, fut in futs.items():
            try:
                results[name] = fut.result(timeout=40)
            except Exception as e:
                results[name] = ([], f"fail:{type(e).__name__}")

    items, status = [], {}
    for name, (its, st) in results.items():
        status[name] = st
        items.extend(its)
    before = len(items)
    # 营销活动物料过滤（平台 campaign 官号内容，非自然舆情；剔除并记录条数）
    promo_n = sum(1 for it in items if _is_promo(it.get("title_or_text") or ""))
    if promo_n:
        items = [it for it in items if not _is_promo(it.get("title_or_text") or "")]
        storage.log(f"营销物料过滤：剔除 {promo_n} 条（campaign/商单标记）")
    # 重复舆情判重（采集即去重：URL/标题指纹完全相同 → 不入库，来源并入 sources）
    from .dedup import dedup_items
    items, dstats = dedup_items(items, cfg)
    if dstats.get("exact_dup"):
        storage.log(f"重复舆情过滤：{before} 条 → {len(items)} 条"
                    f"（精确重复 {dstats['exact_dup']} 条，同源报道合并 {dstats.get('near_dup', 0)} 处）")
    # 英文标题翻译（海外源→中文，事件命名与看板展示用；失败保留英文不影响主流程）
    from .translate import translate_items
    translate_items(items, cfg)
    return items, status


def run_analyze(pom_json: Path, outdir: Path, store: Path) -> Path:
    """调用技能 analyze.py，返回 analyzed.json 路径。"""
    outdir.mkdir(parents=True, exist_ok=True)
    store.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ANALYZE_PY), str(pom_json),
           "--top-n", "10", "--outdir", str(outdir), "--store", str(store)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"analyze.py 失败: {proc.stderr[-500:]}")
    outs = sorted(outdir.glob("pom-analyzed-*.json"))
    if not outs:
        raise RuntimeError("analyze.py 未产出 analyzed.json")
    return outs[-1]


def build_baseline(cfg: dict) -> dict:
    """完整基线流程：采集 -> 统一JSON -> analyze.py -> 新闻事件聚类 -> 快照。"""
    keywords = cfg.get("keywords") or ["AI工具", "AIGC"]
    kw_str = ",".join(keywords)
    items, status = collect_all(cfg)

    pom = storage.INPUTS / f"pom-{storage.now_stamp()}.json"
    storage.write_json(pom, {
        "meta": {"keywords": kw_str, "range": cfg.get("baseline", {}).get("range", "7d"),
                 "platforms": list(status.keys()), "collected_at": storage.now_iso()},
        "items": items,
    })
    if not items:
        storage.log(f"基线采集为空，源状态: {status}")
        return {"ok": False, "error": "所有采集源均无数据", "source_status": status,
                "collected_at": storage.now_iso(), "keywords": kw_str}

    analyzed_p = run_analyze(pom, storage.INPUTS, storage.BASELINES / "snapshots")
    analyzed = storage.read_json(analyzed_p)
    analyzed["ok"] = True
    analyzed["keywords"] = kw_str
    analyzed["source_status"] = status
    analyzed["source_catalog"] = source_summary(cfg)
    analyzed["collected_at"] = storage.now_iso()
    analyzed["pov_events"] = build_news_events(analyzed.get("items") or [])
    analyzed["baseline_file"] = str(storage.save_baseline(analyzed))
    storage.log(f"基线采集完成: {len(items)} 条, 新闻事件 {len(analyzed['pov_events'])} 个, 源状态 {status}")
    return analyzed
