"""舆情条目去重：采集时判定「是不是同一件事」，重复则不入库。

背景：数据源之间天然存在重复——同一条新闻会被搜索引擎、X、HN、多家媒体
同时报道，标题措辞不同但讲的是同一件事；而一条条目又会命中多个话题，
导致事件库里同一件事被拆成多条重复舆情。

两条防线：
  ① 条目级（collect 时）：完全重复的条目（URL 相同 / 标题指纹相同）
     → 不入库，来源并入保留条目的 sources，避免重复计数抬高热度
  ② 事件级（build_news_events）：按「故事」聚类，互为同一件事的条目
     归一簇 → 一簇 = 一个事件，消除「同一新闻 × 多个话题」的重复展示

相似度算法：IDF 加权余弦。
  纯字符 bigram Jaccard 对长中文标题过于苛刻——「REA 三天狂揽12万星」
  与「REA 登顶 GitHub Trending」这类同源报道 Jaccard 仅 0.1；
  改用 IDF 加权后，「视频/制作/工具」这类高频泛词权重被压低，
  而「逆向/万星/roadster」这类稀有实体词主导相似度。
"""
import math
import re
from collections import defaultdict

from .feedkit import _parse_dt

# 归一化时剔除的字符（标点/空白/全角符号）
_PUNCT_RE = re.compile(r"[\s　!-/:-@\[-`{-~ -⁯、-〿！-･]+")
# 中文连续片段 / 英文数字词
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_EN_WORD = re.compile(r"[a-z0-9][a-z0-9.\-+]{1,}")
# 采集器给同一来源页的多条新闻加的片段标记（#s0/#s1…），属于条目区分符，需保留
_SYNTH_FRAG = re.compile(r"^s\d+$", re.I)

# 英文停用词（高频泛词，不参与相似度判定）
_STOP_EN = {
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

DEFAULT_SIM = 0.30          # IDF 余弦：同故事判重阈值
DEFAULT_WINDOW = 72         # 同故事允许的最大发布间隔（小时，与 EVENT_GAP_HOURS 一致）
DEFAULT_EXACT_ONLY = True   # 条目级：仅精确重复不入库（近似重复保留为同一事件的多条报道）

# 通用前缀：HN 的 Show HN / Ask HN、MIT TR 的 The Download 等栏目名。
# 这些词出现频次低（IDF 高）却无区分度，会把同栏目的不同新闻误判为同一件事。
_GENERIC_PREFIX = re.compile(
    r"^\s*(show\s+hn|ask\s+hn|launch\s+hn|the\s+download|hard\s+fork)"
    r"\s*[：:。·\-–—|｜]\s*", re.I)


def _norm(s: str) -> str:
    """归一化：小写 + 去标点/空白 + 全角转半角。"""
    t = (s or "").lower()
    t = t.translate(str.maketrans("！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～",
                                  "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"))
    return _PUNCT_RE.sub("", t)


def title_sig(title: str) -> str:
    """标题指纹（归一化后的字符串，用于精确判重）。"""
    return _norm(title)


def _clean(s: str) -> str:
    """去掉栏目前缀等无区分度的通用开头。"""
    return _GENERIC_PREFIX.sub("", (s or "").strip())


def _grams(text: str) -> set:
    """文本特征：中文 bigram + 英文/数字词（≥2 字符，去停用词）。"""
    text = _clean(text)
    t = (text or "").lower()
    g = set()
    for m in _EN_WORD.finditer(t):
        w = m.group(0).strip(".-+")
        if len(w) >= 2 and w not in _STOP_EN:
            g.add(w)
    for run in _CJK_RUN.findall(text or ""):
        for i in range(len(run) - 1):
            g.add(run[i:i + 2])
    return g


def _url_key(url: str) -> str:
    """URL 归一化键：去协议/www/查询/尾斜杠；保留采集器合成的 #sN 区分符。

    同一来源页常被拆成多条新闻（各自 #s0/#s1…），若把片段一并剥掉，
    这些独立条目会被误判为重复而丢弃。
    """
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    frag = ""
    if "#" in u:
        u, frag = u.split("#", 1)
    u = u.split("?")[0].rstrip("/")
    if _SYNTH_FRAG.match(frag or ""):
        u = f"{u}#{frag.lower()}"
    return u


class _IDF:
    """语料 IDF + 向量化 + 余弦/包含度（一次性构建，避免重复计算）。"""

    def __init__(self, texts: list):
        df = defaultdict(int)
        self.grams = []
        for t in texts:
            g = _grams(t)
            self.grams.append(g)
            for term in g:
                df[term] += 1
        n = max(1, len(texts))
        self.df = df
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.N = n
        self.vecs = []
        for g in self.grams:
            v = {t: self.idf.get(t, 0.0) for t in g}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            self.vecs.append((v, norm))

    def cos(self, i: int, j: int) -> float:
        """IDF 加权余弦（对长度差异敏感，适合整体措辞接近的判定）。"""
        a, na = self.vecs[i]
        b, nb = self.vecs[j]
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a           # 遍历较小集合
        s = sum(w * b.get(t, 0.0) for t, w in a.items())
        return s / (na * nb)

    def containment(self, i: int, j: int) -> float:
        """IDF 加权包含度：交集权重 / 较小集合权重。

        短标题被长标题包含时得分高——「Tesla Roadster」与
        「特斯拉再次尝试 Roadster…」这类同源报道能被识别。
        """
        inter = sum(self.idf.get(t, 0.0) for t in (self.grams[i] & self.grams[j]))
        ma = sum(self.idf.get(t, 0.0) for t in self.grams[i]) or 1.0
        mb = sum(self.idf.get(t, 0.0) for t in self.grams[j]) or 1.0
        return inter / min(ma, mb)

    def shared_rare(self, i: int, j: int, max_df_ratio: float = 0.15) -> int:
        """共享的稀有实体词数量（df 占比 ≤ max_df_ratio 才算稀有）。"""
        cap = max(1, int(self.N * max_df_ratio))
        shared = self.grams[i] & self.grams[j]
        return sum(1 for t in shared if self.df.get(t, 0) <= cap)


def _eng(it: dict) -> int:
    m = it.get("metrics") or {}
    try:
        return int(m.get("engagement_total") or m.get("likes", 0) + m.get("comments", 0))
    except (TypeError, ValueError):
        return 0


def _merge_into(keep: dict, drop: dict, reason: str):
    """把重复条目 drop 的信息并入保留条目 keep（来源与话题不丢）。"""
    srcs = keep.get("sources")
    if not isinstance(srcs, list):
        srcs = [{"url": keep.get("url") or "", "platform": keep.get("platform") or ""}]
    srcs.append({"url": drop.get("url") or "", "platform": drop.get("platform") or ""})
    keep["sources"] = srcs
    keep["dup_count"] = int(keep.get("dup_count") or 1) + 1
    tp = list(keep.get("topics") or [])
    for t in (drop.get("topics") or []):
        if t not in tp:
            tp.append(t)
    keep["topics"] = tp


def _close(a: dict, b: dict, win_h: float) -> bool:
    """发布时间是否接近。任一方缺时间 → 视为不可靠（调用方提高阈值）。"""
    da, db = _parse_dt(a.get("published_at")), _parse_dt(b.get("published_at"))
    if not da or not db:
        return False
    return abs((da - db).total_seconds()) <= win_h * 3600


def dedup_items(items: list, cfg: dict | None = None) -> tuple:
    """采集时条目去重：完全重复的条目不再入库。

    - 精确重复：URL 归一化后相同，或标题指纹完全相同 → 丢弃，来源并入 sources
    - 近似重复（不同来源报道同一件事）：默认**保留**为独立报道条目，
      由 build_news_events 聚成同一事件（置 dedup.near_dup_merge=true 可直接合并）

    返回 (uniq_items, stats)；stats 含 kept/exact_dup/near_dup 与明细，便于审计。
    """
    dcfg = (cfg or {}).get("dedup") or {}
    if dcfg.get("enabled") is False:
        return list(items or []), {"kept": len(items or []), "exact_dup": 0,
                                   "near_dup": 0, "details": []}

    sim_th = float(dcfg.get("similarity", DEFAULT_SIM))
    win_h = float(dcfg.get("time_window_hours", DEFAULT_WINDOW))
    merge_near = bool(dcfg.get("near_dup_merge", not DEFAULT_EXACT_ONLY))

    items = list(items or [])
    texts = [(it.get("title_zh") or it.get("title_or_text") or "") for it in items]
    idf = _IDF(texts)

    kept, exact, near, details = [], 0, 0, []
    by_url, by_sig = {}, {}

    def _title_of(it):
        return (it.get("title_zh") or it.get("title_or_text") or "")

    kept_idx = []            # 保留条目在 items 中的原始下标
    for idx, it in enumerate(items):
        uk = _url_key(it.get("url"))
        sk = title_sig(_title_of(it))

        # ---- ① 精确重复：不入库，来源并入保留条目
        if uk and uk in by_url:
            _merge_into(by_url[uk], it, "url")
            exact += 1
            details.append({"dup": _title_of(it)[:40], "reason": "url"})
            continue
        if sk and sk in by_sig:
            _merge_into(by_sig[sk], it, "title")
            exact += 1
            details.append({"dup": _title_of(it)[:40], "reason": "title"})
            continue

        # ---- ② 近似重复：默认保留为独立报道（热度和来源不丢）
        hit = None
        for kidx in kept_idx:
            if idf.cos(idx, kidx) < sim_th:
                continue
            other = items[kidx]
            if not _close(it, other, win_h):
                continue
            hit = kidx
            break
        if hit is not None:
            if merge_near:
                _merge_into(items[hit], it, "similar")
                near += 1
                details.append({"dup": _title_of(it)[:40], "reason": "similar"})
                continue
            near += 1                 # 仅计数，不合并（事件层会归为同一故事）

        kept.append(it)
        kept_idx.append(idx)
        if uk:
            by_url[uk] = it
        if sk:
            by_sig[sk] = it

    return kept, {"kept": len(kept), "exact_dup": exact, "near_dup": near,
                  "details": details}


class _DSU:
    """并查集（用于故事聚类）。"""

    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def same_story(idf: _IDF, i: int, j: int, dts: list, sim_th: float,
               win_h: float) -> bool:
    """两条条目是不是「同一件事」。

    判定：发布时间间隔 ≤ window 且 IDF 余弦相似度 ≥ sim_th。
    缺时间的条目抬高门槛（≥0.88），只与极相似条目聚合，避免误并。

    （更早版本还有「共享稀有实体词」兜底，实测会把 Meta Muse Spark 与
      FLUX 这类「都是模型发布稿」的不同新闻误并，故去掉。）
    """
    da, db = dts[i], dts[j]
    undated = not (da and db)
    if undated:
        return idf.cos(i, j) >= max(sim_th, 0.88)
    if abs((da - db).total_seconds()) > win_h * 3600:
        return False
    return idf.cos(i, j) >= sim_th


def cluster_stories(items: list, cfg: dict | None = None) -> list:
    """把条目聚成「故事」簇（同一件事的不同报道归一簇）。

    用并查集把互为同一件事的条目连通，一簇 = 一个新闻事件——
    从而消除「一条新闻命中 N 个话题 → 生成 N 个重复事件」。
    返回 [[item, ...], ...]
    """
    dcfg = (cfg or {}).get("dedup") or {}
    sim_th = float(dcfg.get("similarity", DEFAULT_SIM))
    win_h = float(dcfg.get("time_window_hours", DEFAULT_WINDOW))

    items = list(items or [])
    n = len(items)
    if n <= 1:
        return [items] if items else []

    texts = [(it.get("title_zh") or it.get("title_or_text") or "") for it in items]
    dts = [_parse_dt(it.get("published_at")) for it in items]
    idf = _IDF(texts)

    dsu = _DSU(n)
    for i in range(n):
        for j in range(i + 1, n):
            if same_story(idf, i, j, dts, sim_th, win_h):
                dsu.union(i, j)

    groups = defaultdict(list)
    for idx in range(n):
        groups[dsu.find(idx)].append(items[idx])
    return list(groups.values())


def pick_topic(members: list) -> str:
    """簇的主话题：优先产品话题（更具体），其次出现频次最高的概念话题。"""
    from .lexicon import PRODUCT_LEXICON, CONCEPT_LEXICON
    cnt = defaultdict(int)
    for it in members:
        for t in set(it.get("topics") or []):
            cnt[t] += 1
    if not cnt:
        return "其他"
    products = [t for t in cnt if t in PRODUCT_LEXICON]
    pool = products or [t for t in cnt if t in CONCEPT_LEXICON] or list(cnt)
    return max(pool, key=lambda t: (cnt[t], -len(t)))
