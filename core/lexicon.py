"""AI工具/AIGC 领域词典：选题标签抽取与话题归一化。

与 public-opinion-monitor 技能同款确定性思路：不做概率分词，
用「别名 -> 标准话题」字典命中 + 二元组兜底。
匹配时忽略空格与大小写（"AI 配音" == "ai配音"）。
"""

import re
from pathlib import Path

# 产品/工具话题：标准名 -> 别名列表（小写比对）
PRODUCT_LEXICON = {
    "ChatGPT": ["chatgpt", "chatgpt4", "gpt-4", "gpt4", "gpt-5", "gpt5",
                "gpt-6", "gpt6", "chat gpt"],
    "Claude": ["claude", "opus", "sonnet", "haiku"],
    "Gemini": ["gemini"],
    "DeepSeek": ["deepseek", "深度求索"],
    "Kimi": ["kimi"],
    "豆包": ["豆包"],
    "通义千问": ["通义千问", "通义", "qwen"],
    "文心一言": ["文心一言", "文心"],
    "智谱GLM": ["智谱", "glm"],
    "Sora": ["sora"],
    "即梦": ["即梦", "jimeng", "seedance"],
    "可灵": ["可灵", "kling"],
    "海螺AI": ["海螺", "hailuo"],
    "Vidu": ["vidu"],
    "Midjourney": ["midjourney"],
    "Stable Diffusion": ["stable diffusion", "sd模型"],
    "ComfyUI": ["comfyui"],
    "剪映": ["剪映", "capcut"],
    "Cursor": ["cursor"],
    "GitHub Copilot": ["copilot"],
    "Suno": ["suno"],
    "Manus": ["manus"],
    "Perplexity": ["perplexity"],
    "Runway": ["runway"],
    "Luma": ["luma"],
    "Veo": ["veo"],
    "MiniMax": ["minimax"],
    "腾讯元宝": ["元宝"],
    "讯飞星火": ["星火"],
    "阶跃星辰": ["阶跃", "stepfun"],
    "ChatTTS": ["chattts"],
    "Higgsfield": ["higgsfield"],
    "Pika": ["pika"],
    # 2026-10 新增热点产品
    "REA": ["rea", "reverse engineer anything", "逆向一切"],
    "Muse Spark": ["muse spark"],
    "Nemotron": ["nemotron"],
    "MiMo": ["mimo"],
    "Ultramarine": ["ultramarine"],
    "Nano Banana": ["nano banana"],
    "WinClaw": ["winclaw"],
    "LibTV": ["libtv"],
    "ArtCraft": ["artcraft", "wordcraft", "gridcraft", "deckcraft"],
    "FLUX": ["flux"],
    # 科技前沿热点（用户关注面：马斯克/特斯拉动态）
    "特斯拉": ["特斯拉", "tesla"],
    "Roadster": ["roadster"],
    "马斯克": ["马斯克", "musk", "埃隆"],
}

# 概念/场景话题
CONCEPT_LEXICON = {
    "AIGC": ["aigc"],
    "文生视频": ["文生视频", "t2v", "文字生成视频"],
    "图生视频": ["图生视频", "i2v"],
    "AI绘画": ["ai绘画", "ai绘图", "文生图", "ai画图"],
    "AI短剧": ["ai短剧", "ai短片", "ai动画", "ai漫剧", "漫剧"],
    "数字人": ["数字人", "虚拟人"],
    "智能体": ["智能体", "ai agent"],
    "大模型": ["大模型", "大语言模型", "llm"],
    "多模态": ["多模态"],
    "开源模型": ["开源模型", "开源大模型", "开放权重"],
    "模型量化": ["模型量化", "量化部署"],
    "MCP协议": ["mcp"],
    "工作流": ["工作流", "workflow"],
    "AI编程": ["ai编程", "vibe coding", "ai写代码", "ai coding"],
    "AI搜索": ["ai搜索", "ai搜索引擎"],
    "AI硬件": ["ai硬件", "ai玩具", "ai眼镜", "ai机器人"],
    "提示词": ["提示词", "prompt工程", "prompt"],
    "AI音乐": ["ai音乐", "ai作曲", "ai写歌"],
    "AI配音": ["ai配音", "语音合成"],
    "AI写作": ["ai写作", "ai文案", "ai写文章"],
    "AI PPT": ["ai ppt", "ai做ppt", "ai幻灯片"],
    "AI视频生成": ["ai视频生成", "视频生成模型"],
    "AI变现": ["ai变现", "ai副业", "ai赚钱", "用ai赚钱"],
    "AI教育": ["ai教育", "ai学习", "ai教学"],
    # 平台治理/账号风波（Claude 封号潮类事件的聚合话题）
    "账号封禁": ["封号", "封号潮", "账号封禁", "账户封禁", "封禁账号",
               "账号被封", "account ban", "account suspension", "banned account"],
}

LEXICON = {**PRODUCT_LEXICON, **CONCEPT_LEXICON}

# 话题 -> 归类（供 GUI 分组展示）
TOPIC_GROUPS = {
    "product": list(PRODUCT_LEXICON.keys()),
    "concept": list(CONCEPT_LEXICON.keys()),
}

_SPACE_RE = re.compile(r"\s+")

_DYN_FILE = Path(__file__).resolve().parent.parent / "data" / "dynamic_lexicon.json"


def load_dynamic_lexicon() -> dict:
    """进化学到的动态词库 {标准话题: [别名]}（core/evolution 写入）。

    博主内容反推出采集缺口时，把新产品/新实体词学进来，
    下轮采集的热榜/RSS 条目即可被 extract_topics 打上话题标签。
    """
    try:
        import json
        d = json.loads(_DYN_FILE.read_text(encoding="utf-8"))
        return {k: [str(a) for a in v] for k, v in d.items()
                if isinstance(v, list) and v}
    except (OSError, ValueError):
        return {}


def _norm(s: str) -> str:
    """小写 + 去空格，消除 'AI 配音' vs 'ai配音' 的匹配差异。"""
    return _SPACE_RE.sub("", (s or "").lower())


def _alias_hit(alias: str, text_low: str, text_norm: str) -> bool:
    """别名命中判定。

    中文/长别名：归一化后子串匹配（"AI 配音" 命中 "ai配音"）。
    ≤5 字符的纯 ASCII 短别名（rea/veo/pika/suno/mimo/libtv/flux…）：
    词边界匹配，避免 'rea' 误伤 real/great/create、'pika' 误伤 pikachu。
    词边界在原始小写文本上判定（保留空格），中文文本不受影响。
    """
    a_norm = _norm(alias)
    if a_norm.isascii() and len(a_norm) <= 5:
        return re.search(r"(?<![a-z0-9])" + re.escape(a_norm) + r"(?![a-z0-9])",
                         text_low) is not None
    return a_norm in text_norm


def extract_topics(text: str) -> list:
    """从文本抽取命中的标准话题列表（保持词典定义序）。

    词典 = 内置 LEXICON + 进化学到的动态词库（data/dynamic_lexicon.json）。
    """
    if not text:
        return []
    low = (text or "").lower()
    text_norm = _norm(text)
    if not text_norm:
        return []
    hits = []
    merged = LEXICON
    dyn = load_dynamic_lexicon()
    if dyn:
        merged = {**LEXICON, **dyn}
    for canonical, aliases in merged.items():
        for alias in aliases:
            if _alias_hit(alias, low, text_norm):
                hits.append(canonical)
                break
    return hits


def bigrams(s: str) -> set:
    s = re.sub(r"[\s\W_]+", "", (s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
