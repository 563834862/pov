"""抖音博主内容采集：Playwright 浏览器桥接（复用用户 Chrome 登录态）。

为什么走桥接：
  - 抖音 Web API（aweme/v1/web/aweme/post）有 Argus 风控，需 a_bogus 签名，直连被拦截
  - 博主主页的作品列表由客户端签名 API 加载，不在 RENDER_DATA 中
  → 按本机规范走 Playwright MCP Bridge 扩展，打开用户自己的 Chrome 访问主页，
    从 DOM 作品卡片提取数据。

提取字段：
  - 视频 ID（aweme_id）：雪花 ID，(id >> 32) 即发布时间的 Unix 秒级时间戳（已验证）
  - 标题：卡片文本去「置顶/点赞前缀」+ 去嵌套双倍文本
  - 点赞数：卡片文本前缀（如 "11.8万" → 118000）
  - 是否置顶（置顶视频通常是较早内容，保留但标记）

用法（被 blogger_monitor 调用）：
  collect_all(bloggers, existing_ids) -> (new_posts, status)
"""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import storage

DRIVER = Path.home() / ".workbuddy" / "bin" / "pw_mcp.mjs"
NODE = Path(r"C:\Users\Administrator.SY-202311081432\.workbuddy\binaries\node\versions\22.22.2-2\node.exe")

# 从博主主页作品卡片提取视频列表
EXTRACT_JS = r"""() => {
  const anchors = [...document.querySelectorAll('a[href*="/video/"]')];
  const seen = new Set();
  const out = [];
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/video\/(\d+)/);
    if (!m) continue;
    const id = m[1];
    if (seen.has(id)) continue;
    seen.add(id);
    let text = (a.textContent || '').replace(/\s+/g, ' ').trim();
    let pinned = false, likes = 0;
    const pm = text.match(/^(置顶)?(\d+(?:\.\d+)?)(万?)/);
    if (pm) {
      pinned = !!pm[1];
      likes = Math.round(parseFloat(pm[2]) * (pm[3] === '万' ? 10000 : 1));
      text = text.slice(pm[0].length);
    }
    // 卡片文本常出现标题整体重复两次（嵌套元素），精确双倍时去重
    const half = Math.floor(text.length / 2);
    if (text.length >= 8 && text.length % 2 === 0 && text.slice(0, half) === text.slice(half)) {
      text = text.slice(0, half);
    }
    out.push({id: id, title: text.slice(0, 150), likes: likes, pinned: pinned});
  }
  return out.slice(0, 14);
}"""


def _id_to_datetime(aweme_id: str):
    """抖音雪花视频 ID → 发布时间（id >> 32 为 Unix 秒）。"""
    try:
        ts = int(aweme_id) >> 32
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if 2020 < dt.year < 2030:
            return dt.isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        pass
    return ""


def _build_steps(bloggers: list, batch_tag: str, wait_s: float = 4.0) -> list:
    steps = []
    for i, b in enumerate(bloggers):
        steps.append({"tool": "browser_navigate", "args": {"url": b["url"]}})
        steps.append({"tool": "browser_wait_for", "args": {"time": wait_s}})
        steps.append({"tool": "browser_evaluate", "args": {
            "element": f"{b['name']} 作品列表",
            "function": EXTRACT_JS,
            "filename": f"logs/douyin-{batch_tag}-{i}.json",
        }})
    return steps


def _run_bridge(steps: list, tag: str) -> bool:
    steps_path = storage.LOGS / f"bridge-steps-{tag}.json"
    with open(steps_path, "w", encoding="utf-8") as f:
        json.dump(steps, f, ensure_ascii=False)
    cmd = [str(NODE), str(DRIVER), str(steps_path), "--extension"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                              cwd=str(storage.ROOT))
        storage.log(f"桥接批次 {tag} 完成 rc={proc.returncode}")
        return True
    except subprocess.TimeoutExpired:
        storage.log(f"桥接批次 {tag} 超时")
        return False
    except Exception as e:
        storage.log(f"桥接批次 {tag} 异常: {e}")
        return False


def _parse_results(bloggers: list, batch_tag: str) -> list:
    """读取各博主的结果文件 → 帖子列表。"""
    posts = []
    for i, b in enumerate(bloggers):
        fp = storage.ROOT / "logs" / f"douyin-{batch_tag}-{i}.json"
        if not fp.exists():
            continue
        try:
            videos = storage.read_json(fp, default=[]) or []
        except Exception:
            continue
        for v in videos:
            vid = str(v.get("id") or "")
            if not vid.isdigit():
                continue
            posts.append({
                "id": f"dy_{vid}",
                "blogger": b["name"],
                "platform": "抖音",
                "title": (v.get("title") or "").strip(),
                "text": "",
                "url": f"https://www.douyin.com/video/{vid}",
                "published_at": _id_to_datetime(vid),
                "metrics": {"likes": int(v.get("likes") or 0), "comments": 0, "shares": 0},
                "topics": [],
                "simulated": False,
                "pinned": bool(v.get("pinned")),
                "first_seen_at": storage.now_iso(),
            })
    return posts


def collect_all(bloggers: list, existing_ids: set, batch_size: int = 6) -> tuple:
    """分批经桥接采集所有抖音博主的最新作品。失败批次自动重试一次。"""
    from .lexicon import extract_topics

    all_posts, status = [], {}
    batches = [bloggers[i:i + batch_size] for i in range(0, len(bloggers), batch_size)]
    for bi, batch in enumerate(batches):
        tag = f"b{bi}"
        _run_bridge(_build_steps(batch, tag), tag)
        posts = _parse_results(batch, tag)
        # 空结果的博主重试一次
        got = {p["blogger"] for p in posts}
        retry = [b for b in batch if b["name"] not in got]
        if retry:
            storage.log(f"批次 {tag}: {len(retry)} 个博主无结果，重试")
            _run_bridge(_build_steps(retry, tag + "r"), tag + "r")
            posts += _parse_results(retry, tag + "r")
        for b in batch:
            cnt = sum(1 for p in posts if p["blogger"] == b["name"])
            status[b["name"]] = f"douyin:{cnt}"
        all_posts.extend(posts)

    # 话题抽取 + 去重（已见 ID 跳过）
    new_posts = []
    for p in all_posts:
        p["topics"] = extract_topics(p["title"] + " " + (p.get("text") or ""))
        if p["id"] not in existing_ids:
            new_posts.append(p)
    return new_posts, status
