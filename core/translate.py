"""英文标题翻译层：把海外源（HN/TechCrunch/Ars/X/PH…）的英文标题译成中文。

为什么需要：舆情事件由条目标题命名，英文标题让中文用户「看不出来舆情事件
是什么」。翻译在采集完成后批量进行，结果写入条目 `title_zh` 字段；
事件聚类与看板/报告优先展示 `title_zh`，话题匹配仍用原文（产品名不受影响）。

通道：StepFun（阶跃星辰）chat API，凭证从 ~/.workbuddy/models.json 读取
（url 含 stepfun.com 的条目），无需在项目里硬编码 key。
缓存：data/translate-cache.json（原文→译文），跨运行复用、可人工审校。
失败兜底：API 不可用/超时 → 保留英文原文，不影响采集主流程。
"""
import json
import re
import time
import urllib.request
from pathlib import Path

from . import storage

CACHE_PATH = storage.DATA / "translate-cache.json"

# 术语表（保证全文译名统一；产品名/公司名保留英文由提示词约束）
GLOSSARY = (
    "open-weight=开放权重, frontier model=前沿模型, AI agent=AI 智能体, "
    "MCP=模型上下文协议(MCP), prompt=提示词, fine-tune=微调, multimodal=多模态, "
    "benchmark=基准测试, open-source=开源, repository=仓库, star=星标, "
    "ban=封禁, account=账号, subscription=订阅, developer=开发者, "
    "distillation=蒸馏, weights=权重, inference=推理, launch=发布, "
    "funding=融资, acquisition=收购, lawsuit=诉讼, autonomous driving=自动驾驶"
)

BATCH_SIZE = 8
TIMEOUT = 60
RETRIES = 2


def _is_english(text: str) -> bool:
    """标题主体为英文才翻译（中文/中英混排跳过，避免无谓调用）。"""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    ascii_ratio = sum(1 for c in letters if ord(c) < 128) / len(letters)
    return ascii_ratio > 0.85


def _load_cache() -> dict:
    return storage.read_json(CACHE_PATH, default={}) or {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except OSError:
        pass


def _stepfun_cred():
    """从 ~/.workbuddy/models.json 找 StepFun 凭证（url 含 stepfun.com）。"""
    p = Path.home() / ".workbuddy" / "models.json"
    try:
        entries = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for e in entries or []:
        url = (e.get("url") or "")
        if "stepfun.com" in url and e.get("apiKey"):
            base = url.split("/step_plan")[0].rstrip("/")
            return {"url": f"{base}/v1/chat/completions",
                    "key": e["apiKey"], "model": e.get("id") or "step-5-preview"}
    return None


def _call_api(cred: dict, prompt: str) -> str:
    """StepFun chat 调用（urllib 直连，绕过系统代理）。"""
    body = json.dumps({
        "model": cred["model"],
        "messages": [{"role": "user", "content": prompt}],
        # step-5-preview 是推理模型：reasoning 会先消耗 token，给足余量
        # 否则 content 为空（finish=length）
        "max_tokens": 3000,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        cred["url"], data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cred['key']}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.loads(r.read().decode("utf-8"))
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    return (msg.get("content") or "").strip()


def _translate_batch(cred: dict, texts: list) -> dict:
    """批量翻译（编号行进出）。返回 {原文: 译文}，失败项不在结果里。"""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = (
        "你是 AI 领域科技新闻翻译。把下面的英文标题翻译成简体中文。\n"
        "规则：\n"
        "1. 每行一条，保持行首编号，只输出译文，不要解释、不要加引号\n"
        "2. 产品名/公司名/模型名保留英文（如 Claude、GPT-6、Roadster、REA、Show HN）\n"
        f"3. 术语统一：{GLOSSARY}\n"
        "4. 风格简洁，符合中文科技媒体标题习惯\n"
        f"输入（共 {len(texts)} 条）：\n{numbered}"
    )
    for attempt in range(RETRIES):
        try:
            out = _call_api(cred, prompt)
        except Exception:
            time.sleep(1.5 * (attempt + 1))
            continue
        result = {}
        for line in out.splitlines():
            m = re.match(r"^\s*(\d+)\s*[.、．]\s*(.+)$", line.strip())
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(texts):
                zh = m.group(2).strip().strip('"').strip()
                if zh:
                    result[texts[idx]] = zh
        if len(result) >= max(1, len(texts) // 2):   # 过半成功即采纳
            return result
        time.sleep(1.5 * (attempt + 1))
    return {}


def translate_items(items: list, cfg: dict | None = None) -> tuple:
    """给条目补 `title_zh` 字段（英文标题→中文）。返回 (翻译数, 失败数)。

    在 collect_all 的过滤之后调用：话题抽取已在原文上完成，不受影响。
    """
    tcfg = (cfg or {}).get("translate") or {}
    if tcfg.get("enabled") is False:
        return 0, 0
    cache = _load_cache()
    todo = []
    for it in items:
        title = it.get("title_or_text") or ""
        if not _is_english(title):
            continue
        if title in cache:
            it["title_zh"] = cache[title]
        else:
            todo.append(title)
    todo = list(dict.fromkeys(todo))       # 去重保序
    if not todo:
        return 0, 0

    cred = _stepfun_cred()
    if not cred:
        storage.log("标题翻译：未找到 StepFun 凭证（~/.workbuddy/models.json），保留英文")
        return 0, len(todo)

    done, failed = 0, 0
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        got = _translate_batch(cred, batch)
        for t in batch:
            if t in got:
                cache[t] = got[t]
                done += 1
                for it in items:
                    if (it.get("title_or_text") or "") == t and not it.get("title_zh"):
                        it["title_zh"] = got[t]
            else:
                failed += 1
    _save_cache(cache)
    storage.log(f"标题翻译：API 译 {done} 条，失败 {failed} 条（失败保留英文）")
    return done, failed
