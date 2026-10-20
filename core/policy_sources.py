"""政策与全球事件采集器：Federal Register / 中国政府网政策文件库 / GDELT DOC 2.0。

定位（引自《舆情与AI新闻采集来源总清单》第一、二、三章）：
    政策是**长周期热点**——一条 AI 备案办法、一次出口管制调整，
    会在数周内持续产生解读、解读的解读、以及博主的选题。
    把政策层接进基线，才能判断博主的选题是「蹭短期热点」还是「卡政策红利」。

三个源全部为免 key 官方接口，符合清单「官方 API 优先」原则：

    ★ Federal Register    美联邦规则/行政令/关税，官方 API 完全免费无 key
    ★ 中国政府网政策文件库  国务院政策、行政法规、部门规章（HTML 接口，容错解析）
    ★ GDELT DOC 2.0       全球事件数据库，免 key，15 分钟更新，覆盖 100+ 语种

关于 GDELT 的情绪分（实测澄清，2026-10）：
    清单第七章把 GDELT 描述为「含情绪分数」，但 2.0 的 ``doc/doc`` API 在
    ``mode=artlist`` 下**不返回 tone 字段**（实测返回字段仅 url/url_mobile/
    title/seendate/socialimage/domain/language/sourcecountry）。
    tone 只在 ``mode=timelinetone``（话题级时间序列）或 GKG 离线数据集里提供。
    故本源的价值定位为**全球覆盖广度**（多语种、跨国别报道），
    情绪维度不在此伪造——条目 sentiment 维持 neutral。
"""
import re
from datetime import datetime, timezone

from .feedkit import _is_promo, _mk_item, clean_text, http_get_json
from .overseas_collector import _coerce_dt, _is_ai_any, _iso

# 政策类条目统一打标，便于报告/看板按「政策 vs 市场」分层
POLICY_TAG = "政策监管"

# Federal Register 检索词（可在 config.yaml 的 sources_extra.fedreg_terms 覆盖）
DEFAULT_FEDREG_TERMS = ["artificial intelligence", "machine learning", "semiconductor"]

# GDELT 检索式（OR 连接，15 分钟粒度；可在 config 覆盖）
DEFAULT_GDELT_QUERY = '("artificial intelligence" OR "generative AI" OR "AI model")'

# 中国政府网政策文件库检索词（公文标题口径；可在 config 覆盖）
DEFAULT_GOV_TERMS = ["人工智能", "大模型", "生成式人工智能"]

_SEENDATE_RE = re.compile(r"^(\d{8})T(\d{6})Z$")


# ---------------------------------------------------------------- Federal Register
def collect_federal_register(keywords: list, top_n: int, terms: list = None) -> tuple:
    """美联邦规则/行政令（官方 API，无 key）。"""
    ts = terms or DEFAULT_FEDREG_TERMS
    items, seen = [], set()
    for term in ts[:2]:
        url = ("https://www.federalregister.gov/api/v1/documents.json?"
               f"conditions%5Bterm%5D={_q(term)}&per_page=20&order=newest"
               "&fields%5B%5D=title&fields%5B%5D=html_url"
               "&fields%5B%5D=publication_date&fields%5B%5D=type&fields%5B%5D=agencies")
        try:
            data = http_get_json(url, timeout=20)
        except Exception as e:
            if not items:
                return [], f"fail:{type(e).__name__}"
            break
        for r in data.get("results") or []:
            title = clean_text(r.get("title") or "")
            if not title or title in seen or not _is_ai_any(title):
                continue
            seen.add(title)
            ags = r.get("agencies") or []
            it = _mk_item(
                platform="federal_register",
                title=title,
                url=r.get("html_url") or "",
                author=(ags[0].get("name") if ags and isinstance(ags[0], dict) else "US Federal Register"),
                published=_iso(_coerce_dt(r.get("publication_date") or "")),
                text=f"联邦公报 · {r.get('type', '')}",
            )
            it["tags"].append(POLICY_TAG)
            items.append(it)
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


# ---------------------------------------------------------------- 中国政府网政策文件库
def collect_gov_policy(keywords: list, top_n: int, terms: list = None) -> tuple:
    """国务院政策文件库检索接口（无官方 API，按容错方式解析）。

    返回结构：``{"searchVO": {"totalCount": N, "listVO": [...]}}``
    条目字段：title（含 ``<em>`` 高亮）/ url / pubtimeStr / summary / puborg

    为什么默认检索词不直接用 ``keywords``：
      政策库按**公文标题**精确检索，而 keywords 是面向媒体舆情的短语
      （如 "AI工具"、"AI变现"），在公文标题里命中率几乎为 0；
      故默认用 ``gov_terms``（人工智能 / 大模型 / 生成式人工智能）。
    """
    ts = (terms or DEFAULT_GOV_TERMS)[:2]
    items, seen = [], set()
    for kw in ts:
        url = ("https://sousuo.www.gov.cn/search-gov/data?"
               f"t=zhengcelibrary_gw&q={_q(kw)}&timetype=&mintime=&maxtime="
               "&sort=pubtime&sortType=1&searchfield=title&pcodeJiguan=&childtype="
               "&subchildtype=&tsbq=&pubtimeyear=&puborg=&pcodeYear=&pcodeNum="
               "&filetype=&p=1&n=10&inpro=&bmfl=&dup=&orpro=")
        try:
            data = http_get_json(url, timeout=20)
        except Exception as e:
            if not items:
                return [], f"fail:{type(e).__name__}"
            break
        sv = (data or {}).get("searchVO") or {}
        for r in sv.get("listVO") or []:
            title = clean_text(r.get("title") or "")
            if not title or title in seen:
                continue
            seen.add(title)
            pub = _pub_from_gov(r)
            it = _mk_item(
                platform="gov_policy",
                title=title,
                url=r.get("url") or "",
                author=clean_text(r.get("puborg") or "中国政府网"),
                published=pub,
                text=clean_text(r.get("summary") or ""),
            )
            it["tags"].append(POLICY_TAG)
            items.append(it)
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


def _pub_from_gov(r: dict) -> str:
    """政府网时间字段容错：ptime 毫秒戳优先，回退 pubtimeStr（2025.08.26）。"""
    pt = r.get("ptime") or r.get("pubtime")
    if pt:
        try:
            return datetime.fromtimestamp(int(pt) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
        except (ValueError, OSError, OverflowError):
            pass
    s = clean_text(r.get("pubtimeStr") or "")
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T12:00:00" if m else ""


# ---------------------------------------------------------------- GDELT DOC 2.0
def _parse_seendate(s: str) -> str:
    m = _SEENDATE_RE.match((s or "").strip())
    if not m:
        return _iso(_coerce_dt(s or ""))
    try:
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return _iso(dt)


def collect_gdelt(keywords: list, top_n: int, query: str = None) -> tuple:
    """GDELT DOC 2.0：全球新闻事件，免 key，15 分钟更新，覆盖 100+ 语种。

    限流为「1 请求 / 5 秒」，故单轮只发 1 次请求；
    命中 429 时退避重试一次，仍失败返回 rate-limited 状态（不计为 fail，
    因为这不是源本身的故障，下一轮通常自动恢复）。

    注意：artlist 模式不返回 tone，故条目情绪维持 neutral——
    宁可留空，也不用关键词硬猜一个假的情绪分。
    """
    q = query or DEFAULT_GDELT_QUERY
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
           f"{_q(q)}&mode=artlist&format=json&maxrecords=40&timespan=24h&sort=datedesc")
    try:
        data = http_get_json(url, timeout=25, retries=1, backoff=5.0)
    except Exception as e:
        return [], f"rate-limited" if "429" in str(e) else f"fail:{type(e).__name__}"
    items = []
    for a in data.get("articles") or []:
        title = clean_text(a.get("title") or "")
        if not title or not _is_ai_any(title):
            continue
        if _is_promo(title):
            continue
        items.append(_mk_item(
            platform="gdelt",
            title=title,
            url=a.get("url") or "",
            author=clean_text(a.get("domain") or "GDELT"),
            published=_parse_seendate(a.get("seendate") or ""),
            text=" · ".join(x for x in (clean_text(a.get("sourcecountry") or ""),
                                        clean_text(a.get("language") or "")) if x),
        ))
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:max(10, top_n * 2)], ("ok" if items else "empty")


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


# ---------------------------------------------------------------- 注册表
POLICY_SOURCES = {
    "federal_register": collect_federal_register,
    "gov_policy": collect_gov_policy,
    "gdelt": collect_gdelt,
}

POLICY_SOURCE_LABELS = {
    "federal_register": "美联邦公报",
    "gov_policy": "中国政府网政策",
    "gdelt": "GDELT全球事件",
}
