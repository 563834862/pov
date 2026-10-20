"""验证报告生成：Markdown + 单文件 HTML。"""
from datetime import datetime, timedelta, timezone

from . import storage
from .timeline import blogger_response_speed
from .verify import (opinion_focus, opinion_signals, blogger_hot_topics,
                     blogger_profiles)

CST = timezone(timedelta(hours=8))


def _fmt_time(iso: str) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(CST).strftime("%m-%d %H:%M")
    except (ValueError, AttributeError):
        return str(iso)[:16]


def build_report(verified: list, baseline: dict, cfg: dict, evo: dict | None = None) -> dict:
    """汇总报告数据（供 Markdown/HTML/GUI 共用）。"""
    focus = opinion_focus(baseline, top_n=10)
    signals = opinion_signals(baseline, top_n=12)
    hot_topics = blogger_hot_topics(verified, top_n=10)
    profiles = blogger_profiles(verified)
    pov_events = baseline.get("pov_events") or []
    speed = blogger_response_speed(verified, pov_events)
    verdict_order = {"踩中热点": 0, "追热稳健": 1, "追热偏早": 2, "追热偏晚": 3,
                     "选题滞后": 4, "独苗选题": 5, "领域外内容": 7, "数据不足": 6}
    posts = sorted(verified, key=lambda p: (verdict_order.get((p.get("verification") or {}).get("verdict"), 9),
                                            -((p.get("verification") or {}).get("total") or 0)))
    sim_count = sum(1 for p in verified if p.get("simulated"))
    off_count = sum(1 for p in verified
                    if (p.get("verification") or {}).get("verdict") == "领域外内容")
    return {
        "generated_at": storage.now_iso(),
        "keywords": baseline.get("keywords") or "",
        "health_score": baseline.get("health_score"),
        "source_status": baseline.get("source_status") or {},
        "focus": focus,
        "signals": signals,
        "hot_topics": hot_topics,
        "profiles": profiles,
        "speed": speed,
        "posts": posts,
        "simulated_count": sim_count,
        "off_domain_count": off_count,
        "total_count": len(verified),
        "evolution": evo or {},
    }


def render_markdown(rep: dict) -> str:
    L = [
        "# 舆情验证报告",
        "",
        f"- 监测关键词：{rep['keywords']}",
        f"- 舆情健康分：{rep['health_score']}",
        f"- 验证内容：{rep['total_count']} 条（其中示例数据 {rep['simulated_count']} 条）",
        f"- 生成时间：{rep['generated_at']}",
        f"- 采集源状态：{rep['source_status']}",
        "",
        "## 一、当前舆情关注点（选题参考）",
        "",
        "| # | 事件 | 阶段 | 条数 | 均互动 | 话题标签 |",
        "|---|------|------|------|--------|----------|",
    ]
    for i, f in enumerate(rep["focus"], 1):
        L.append(f"| {i} | {f['title'][:36]} | {f['phase']} | {f['count']} | {f['avg_eng']:,} | "
                 f"{', '.join(f['topics'][:4])} |")

    L += ["", "## 二、你的关注圈在热议（博主选题面，近30天）", "",
          "从已监控博主的内容聚合——这是你关注的人实际在集中做的选题，", 
          "与上面的外部舆情关注点互为镜像：两边都有的话题 = 关注圈与舆情同频；", 
          "只出现在一边的 = 潜在的内容缺口或差异化机会。", "",
          "| # | 话题 | 近30天帖数 | 涉及博主 | 追热命中率 | 代表内容 |",
          "|---|------|-----------|----------|------------|----------|"]
    for i, h in enumerate(rep["hot_topics"], 1):
        L.append(f"| {i} | {h['topic']} | {h['posts']} | {h['bloggers']} 人 | "
                 f"{h['riding_ratio']:.0%} | {h['sample'][:34]} |")

    L += ["## 三、博主内容验证结果", ""]
    for p in rep["posts"]:
        v = p.get("verification") or {}
        score_txt = f"{v.get('total')} 分" if v.get("total") is not None else "—"
        L += [
            f"### {v.get('emoji', '')} {v.get('verdict', '-')}（{score_txt}）"
            f"｜{p.get('blogger')}｜{_fmt_time(p.get('published_at'))}",
            "",
            f"> {p.get('title')}",
            "",
            f"- 选题话题：{', '.join((v.get('post_topics') or []) or ['未识别'])}",
            f"- 比对结论：{v.get('reason', '')}",
            f"- 建议：{v.get('advice', '')}",
            "",
        ]

    L += ["## 四、博主选题风格画像", "",
          "| 博主 | 平台 | 风格 | 平均分 | 踩中 | 稳健 | 偏早 | 偏晚 | 滞后 | 独苗 |",
          "|------|------|------|--------|------|------|------|------|------|------|"]
    for pr in rep["profiles"]:
        c = pr["counts"]
        L.append(f"| {pr['blogger']} | {pr['platform']} | {pr['style']} | {pr['avg_score'] or '-'} | "
                 f"{c.get('踩中热点', 0)} | {c.get('追热稳健', 0)} | {c.get('追热偏早', 0)} | "
                 f"{c.get('追热偏晚', 0)} | {c.get('选题滞后', 0)} | {c.get('独苗选题', 0)} |")

    L += ["", "## 五、博主追热响应速度榜", "",
          "仅统计命中舆情事件的内容（≥2 个事件），按平均滞后升序——越低说明跟进越快：", "",
          "| 博主 | 命中事件 | 平均滞后 | 最快跟进 | 提前布局 | 24h 内跟进 |",
          "|------|----------|----------|----------|----------|------------|"]
    for r in rep["speed"]:
        L.append(f"| {r['blogger']} | {r['events']} | {r['avg_lag_text']} | "
                 f"{r['fastest_lag_text']} | {r['early_count']} | {r['within24h']} |")
    if not rep["speed"]:
        L.append("| - | - | - | - | - | - |")

    L += ["", "## 六、前沿信号（论文 / 开源动态）", "",
          "来自 arXiv、HuggingFace Daily Papers、GitHub Trending 的超前信号。",
          "它们当下讨论量尚低，但常是下一波热点的源头——",
          "适合提前卡位，不计入上面的追热/滞后判据。", "",
          "| # | 信号 | 类别 | 话题 | 互动 | 出现时间 |",
          "|---|------|------|------|------|----------|"]
    for i, s in enumerate(rep["signals"], 1):
        L.append(f"| {i} | {s['title'][:44]} | {'/'.join(s['classes']) or '-'} | "
                 f"{', '.join(s['topics'][:3]) or '-'} | {s['max_eng']:,} | "
                 f"{_fmt_time(s['first_seen'])} |")
    if not rep["signals"]:
        L.append("| - | - | - | - | - | - |")

    # ---- 七、采集缺口与自我进化 ----
    evo = rep.get("evolution") or {}
    L += ["", "## 七、采集缺口与自我进化", "",
          "博主视频是热点舆情的「下游信号」：多位博主近期都在讲某话题、而舆情基线",
          "却没有对应事件时，判定为**采集缺口**（不是博主独苗）——系统自动把该话题",
          "加入采集关键词，下一轮采集生效，并在后续轮次闭环验证是否被覆盖。", ""]
    if evo.get("top_gaps"):
        L += ["| 话题 | 缺口类型 | 涉及博主 | 帖数 | 判定依据 |",
              "|------|----------|----------|------|----------|"]
        type_label = {"no_event": "无事件", "thin_event": "覆盖薄",
                      "stale_event": "事件陈旧", "tutorial": "常青教程"}
        for g in evo["top_gaps"]:
            L.append(f"| {g['topic']} | {type_label.get(g['gap_type'], g['gap_type'])} | "
                     f"{g['blogger_count']} 人 | {g['post_count']} | {g['reason'][:52]} |")
    else:
        L.append("本轮未检测到采集缺口。")
    resolved = evo.get("resolved") or []
    learned = evo.get("learned") or []
    stale = evo.get("stale") or []
    if resolved:
        L += ["", f"**本轮闭环解决**：{', '.join(resolved)}（新采集已覆盖这些话题）"]
    if learned:
        L += ["", f"**词典新学实体词**：{', '.join(x['canonical'] for x in learned)}"
                   "（下轮采集的热榜/RSS 条目将自动打上这些话题标签）"]
    if stale:
        L += ["", f"**停止追补**：{', '.join(stale)}（连续多轮未采到覆盖，"
                   "判定为教程类常青内容或源不覆盖）"]

    L += ["", "---",
          f"数据来源：POV 舆情验证系统 · 舆情基线来自「{rep['keywords']}」相关公开信息",
          "标注「示例数据」的博主内容为演示用途，非真实发布内容；验证逻辑与结论为真实计算。",
          ""]
    return "\n".join(L)

def render_html(rep: dict) -> str:
    """单文件 HTML 报告（无外部依赖，可直接分享）。"""
    VERDICT_COLOR = {"踩中热点": "#16a34a", "追热稳健": "#d97706", "追热偏早": "#d97706",
                     "追热偏晚": "#d97706", "选题滞后": "#dc2626", "独苗选题": "#2563eb", "数据不足": "#6b7280"}
    PHASE_COLOR = {"发酵期": "#dc2626", "活跃期": "#d97706", "苗头期": "#2563eb",
                   "回落期": "#6b7280", "消退期": "#9ca3af"}

    def esc(s):
        import html
        return html.escape(str(s if s is not None else ""))

    counts = {}
    for p in rep["posts"]:
        v = (p.get("verification") or {}).get("verdict", "数据不足")
        counts[v] = counts.get(v, 0) + 1

    focus_rows = "".join(
        f"<tr><td>{i}</td><td><b>{esc(f['topic'])}</b></td><td>{esc(f['title'][:40])}</td>"
        f"<td><span style='color:{PHASE_COLOR.get(f['phase'], '#666')}'>{esc(f['phase'])}</span></td>"
        f"<td>{f['count']}</td><td>{f['avg_eng']:,}</td></tr>"
        for i, f in enumerate(rep["focus"], 1))

    # ---- 二、关注圈热议（博主选题面） ----
    hot_rows = "".join(
        f"<tr><td>{i}</td><td><b>{esc(h['topic'])}</b></td><td>{h['posts']}</td>"
        f"<td>{h['bloggers']} 人</td><td>{h['riding_ratio']:.0%}</td>"
        f"<td>{esc(h['sample'][:36])}</td></tr>"
        for i, h in enumerate(rep["hot_topics"], 1))
    hot_html = (f"<table><tr><th>#</th><th>话题</th><th>近30天帖数</th><th>涉及博主</th>"
                f"<th>追热命中率</th><th>代表内容</th></tr>{hot_rows}</table>"
                if hot_rows else "<div class='tl-meta'>暂无博主内容数据</div>")

    # ---- 二、跟进时间线已移除（v0.4.3：博主跟进关系改由「舆情事件库」点击查看） ----
    cards = ""
    for p in rep["posts"]:
        v = p.get("verification") or {}
        color = VERDICT_COLOR.get(v.get("verdict"), "#6b7280")
        score = f"{v['total']} 分" if v.get("total") is not None else "—"
        ev = v.get("matched_event") or {}
        ev_line = (f"命中事件「{esc(ev.get('title', ''))[:30]}」· {esc(ev.get('phase'))} · "
                   f"匹配度 {v.get('match_score')}") if ev else "未命中当前热点事件"
        topics = " ".join(f"<span class='chip'>{esc(t)}</span>" for t in (v.get("post_topics") or []))
        cards += f"""
        <div class="card" style="border-left-color:{color}">
          <div class="card-head">
            <span class="badge" style="color:{color};background:{color}1a">{v.get('emoji', '')} {esc(v.get('verdict'))}</span>
            <b>{score}</b>
            <span class="meta">{esc(p.get('blogger'))} · {esc(p.get('platform'))} · {_fmt_time(p.get('published_at'))}</span>
            {'<span class="sim">示例数据</span>' if p.get('simulated') else ''}
          </div>
          <div class="title">{esc(p.get('title'))}</div>
          <div class="chips">{topics}</div>
          <div class="match">{ev_line}</div>
          <div class="advice"><b>建议：</b>{esc(v.get('advice') or v.get('reason') or '')}</div>
        </div>"""

    # ---- 六、前沿信号（论文 / 开源动态） ----
    signal_rows = "".join(
        f"<tr><td>{i}</td><td>{esc(s['title'][:46])}</td>"
        f"<td>{esc('/'.join(s['classes']) or '-')}</td>"
        f"<td>{esc(', '.join(s['topics'][:3]) or '-')}</td>"
        f"<td>{s['max_eng']:,}</td><td>{_fmt_time(s['first_seen'])}</td></tr>"
        for i, s in enumerate(rep["signals"], 1))
    signal_html = (f"<table><tr><th>#</th><th>信号</th><th>类别</th><th>话题</th>"
                   f"<th>互动</th><th>出现时间</th></tr>{signal_rows}</table>"
                   if signal_rows else "<div class='tl-meta'>暂无前沿信号</div>")

    profile_rows = "".join(
        f"<tr><td>{esc(pr['blogger'])}</td><td>{esc(pr['platform'])}</td><td>{esc(pr['style'])}</td>"
        f"<td>{pr['avg_score'] or '-'}</td><td>{pr['counts'].get('踩中热点', 0)}</td>"
        f"<td>{pr['counts'].get('追热稳健', 0)}</td>"
        f"<td>{pr['counts'].get('追热偏早', 0) + pr['counts'].get('追热偏晚', 0)}</td>"
        f"<td>{pr['counts'].get('选题滞后', 0)}</td><td>{pr['counts'].get('独苗选题', 0)}</td></tr>"
        for pr in rep["profiles"])

    speed_rows = "".join(
        f"<tr><td><b>{esc(r['blogger'])}</b></td><td>{r['events']}</td>"
        f"<td><b>{esc(r['avg_lag_text'])}</b></td><td>{esc(r['fastest_lag_text'])}</td>"
        f"<td>{r['early_count']}</td><td>{r['within24h']}</td></tr>"
        for r in rep["speed"]) or "<tr><td colspan='6' style='color:#8a93a6'>暂无足够样本（需命中 ≥2 个舆情事件）</td></tr>"

    chips_kw = "".join(f"<span class='chip'>#{esc(k)}</span>" for k in str(rep["keywords"]).split(","))
    src = " ".join(f"<span class='src {'ok' if s == 'ok' else 'bad'}'>{esc(k)} {'✓' if s == 'ok' else '✗'}</span>"
                   for k, s in (rep["source_status"] or {}).items())

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>舆情验证报告 · {esc(rep['generated_at'][:10])}</title>
<style>
body{{font:14px/1.7 "PingFang SC","Microsoft YaHei",sans-serif;background:#f4f6fa;color:#1f2733;margin:0;padding:24px}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#5b6577;font-size:13px;margin-bottom:16px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}}
.kpi{{background:#fff;border:1px solid #e4e8f0;border-radius:10px;padding:12px 16px}}
.kpi .l{{font-size:12px;color:#5b6577}} .kpi .v{{font-size:24px;font-weight:700}}
.panel{{background:#fff;border:1px solid #e4e8f0;border-radius:10px;padding:16px 18px;margin-bottom:14px}}
h2{{font-size:15px;margin:0 0 10px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid #e4e8f0;white-space:nowrap}}
th{{background:#fafbfe;color:#5b6577;font-size:12px}}
.card{{background:#fbfcfe;border:1px solid #e4e8f0;border-left:4px solid #6b7280;border-radius:8px;padding:12px 14px;margin-bottom:10px}}
.card-head{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:13px}}
.badge{{font-weight:700;border-radius:6px;padding:2px 9px;font-size:12px}}
.meta{{color:#8a93a6;font-size:12px}} .sim{{color:#d97706;background:#fdf3e3;border-radius:4px;padding:1px 6px;font-size:11px}}
.title{{font-weight:600;margin:8px 0 6px;font-size:14.5px}}
.chip{{display:inline-block;color:#2f6bff;background:#eaf0ff;border-radius:4px;padding:1px 8px;font-size:11.5px;margin-right:4px}}
.match{{font-size:12.5px;color:#5b6577;background:#f1f3f7;border-radius:6px;padding:6px 10px;margin-bottom:6px}}
.advice{{font-size:12.5px;color:#5b6577}}
.src{{display:inline-block;font-size:12px;border-radius:4px;padding:1px 8px;margin-right:6px}}
.src.ok{{color:#16a34a;background:#e8f7ee}} .src.bad{{color:#dc2626;background:#fdecec}}
.tl-meta{{color:#8a93a6;font-size:12px}}
.foot{{font-size:11.5px;color:#8a93a6;margin-top:8px}}
</style></head><body><div class="wrap">
<h1>📡 舆情验证报告</h1>
<div class="sub">监测关键词 {chips_kw} · 生成时间 {esc(rep['generated_at'])} · 舆情健康分 <b>{rep['health_score']}</b></div>
<div class="kpis">
  <div class="kpi"><div class="l">验证内容</div><div class="v">{rep['total_count']}</div></div>
  <div class="kpi"><div class="l">🟢 踩中热点</div><div class="v" style="color:#16a34a">{counts.get('踩中热点', 0)}</div></div>
  <div class="kpi"><div class="l">🟡 追热（稳健/偏早/晚）</div><div class="v" style="color:#d97706">{counts.get('追热稳健', 0) + counts.get('追热偏早', 0) + counts.get('追热偏晚', 0)}</div></div>
  <div class="kpi"><div class="l">🔴 滞后 / 🔵 独苗</div><div class="v">{counts.get('选题滞后', 0)} / {counts.get('独苗选题', 0)}</div></div>
  <div class="kpi"><div class="l">⚪ 领域外内容</div><div class="v" style="color:#6b7280">{counts.get('领域外内容', 0)}</div></div>
  <div class="kpi"><div class="l">🧬 采集缺口（进化中）</div><div class="v" style="color:#7c3aed">{(rep.get('evolution') or {}).get('gap_count', 0)}</div></div>
</div>
<div class="panel"><h2>一、当前舆情关注点（选题参考）</h2>
<table><tr><th>#</th><th>话题</th><th>代表事件</th><th>阶段</th><th>报道数</th><th>均互动</th></tr>{focus_rows}</table>
<div class="foot">采集源：{src}</div></div>
<div class="panel"><h2>二、你的关注圈在热议（博主选题面 · 近30天）</h2>{hot_html}
<div class="foot">从已监控博主内容聚合：与「舆情关注点」互为镜像——两边都有 = 关注圈与舆情同频；只在一边 = 内容缺口或差异化机会。</div></div>
<div class="panel"><h2>三、博主内容验证结果（{rep['total_count']} 条，含示例数据 {rep['simulated_count']} 条）</h2>{cards}</div>
<div class="panel"><h2>四、博主选题风格画像</h2>
<table><tr><th>博主</th><th>平台</th><th>风格</th><th>平均分</th><th>踩中</th><th>稳健</th><th>偏早/晚</th><th>滞后</th><th>独苗</th></tr>{profile_rows}</table></div>
<div class="panel"><h2>五、博主追热响应速度榜</h2>
<table><tr><th>博主</th><th>命中事件</th><th>平均滞后</th><th>最快跟进</th><th>提前布局</th><th>24h 内跟进</th></tr>{speed_rows}</table>
<div class="foot">仅统计命中舆情事件的内容（≥2 个事件）；「提前布局」=内容发布早于舆情首发时间。</div></div>
<div class="panel"><h2>六、前沿信号（论文 / 开源动态）</h2>{signal_html}
<div class="foot">来自 arXiv / HF Daily Papers / GitHub Trending 的超前信号：当下讨论量尚低，常是下一波热点源头，适合提前卡位；不计入追热/滞后判据。</div></div>
<div class="foot">数据来源：POV 舆情验证系统 · 标注「示例数据」的博主内容为演示用途，非真实发布内容；验证逻辑与结论为真实计算。</div>
</div></body></html>"""


def save_report(verified: list, baseline: dict, cfg: dict) -> dict:
    rep = build_report(verified, baseline, cfg)
    ts = storage.now_stamp()
    md_path = storage.REPORTS / f"pov-report-{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(rep))
    html_path = storage.REPORTS / f"pov-report-{ts}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(rep))
    storage.write_json(storage.REPORTS / f"pov-report-{ts}.json", rep)
    storage.write_json(storage.REPORTS / "report-latest.json", rep)
    rep["md_path"] = str(md_path)
    rep["html_path"] = str(html_path)
    storage.log(f"报告已生成: {md_path} / {html_path}")
    return rep
