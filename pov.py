#!/usr/bin/env python3
"""POV 舆情验证系统 CLI。

用法：
  python pov.py collect   # 采集舆情基线（多源并行 + 技能分析脚本）
  python pov.py monitor   # 检查博主新内容
  python pov.py verify    # 监控 + 比对验证（写回 posts.json）
  python pov.py report    # 生成验证报告（Markdown + JSON）
  python pov.py run       # 全流程：collect -> verify -> report
  python pov.py serve     # 启动 Web 看板（默认 127.0.0.1:8766，内嵌自动调度）
  python pov.py schedule  # 独立常驻调度循环（不看板，Ctrl+C 停止）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import storage, baseline as bl, blogger_monitor as bm, verify as vf, report as rp, scheduler, evolution  # noqa: E402


def cmd_collect(cfg):
    result = scheduler.job_collect(cfg)
    if result.get("skipped"):
        print(f"跳过采集：{result.get('reason')}")
        return result
    if result.get("ok"):
        print(f"基线完成：{len(result.get('items') or [])} 条，健康分 {result.get('health_score')}，"
              f"话题事件 {len(result.get('pov_events') or [])} 个")
        print(f"源状态：{result.get('source_status')}")
        print(f"快照：{result.get('baseline_file')}")
    else:
        print(f"基线采集失败：{result.get('error')}")
        print(f"源状态：{result.get('source_status')}")
    return result


def cmd_verify(cfg, skip_monitor=False):
    if skip_monitor:
        print(f"跳过博主采集（--no-monitor），复用已采集 {len(storage.load_posts())} 条")
    rep = scheduler.job_verify(cfg, run_monitor=not skip_monitor)
    if not rep or rep.get("skipped") or rep.get("error"):
        print(f"验证未执行：{(rep or {}).get('reason') or (rep or {}).get('error') or '未知原因'}")
        return rep
    verified = storage.load_posts()
    st = storage.load_state()
    st["last_verify_at"] = storage.now_iso()
    storage.save_state(st)
    counts = {}
    for p in verified:
        v = (p.get("verification") or {}).get("verdict", "数据不足")
        counts[v] = counts.get(v, 0) + 1
    print(f"验证完成：{len(verified)} 条内容，结论分布 {counts}")
    print(f"报告：{rep.get('md_path')}")
    return rep


def cmd_serve(cfg):
    """启动 Web 看板。支持 `--port=8766` 覆盖 config.yaml 的端口。内嵌自动调度。"""
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--port", type=int, default=None)
    known, _ = ap.parse_known_args(sys.argv[2:])
    if known.port:
        cfg.setdefault("web", {})["port"] = known.port
    from web.server import serve
    serve(cfg)


def cmd_schedule(cfg):
    """独立常驻调度循环（不开看板）。Ctrl+C 停止。"""
    import time
    if not scheduler.start(cfg):
        print("自动调度已在 config.yaml 中关闭（schedule.enabled=false）")
        return
    print(scheduler.summary(cfg))
    print("调度循环运行中（Ctrl+C 停止）…")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
        print("\n调度已停止")


def main():
    cfg = storage.load_config()
    args = sys.argv[1:]
    cmd = args[0] if args else "run"
    skip_monitor = "--no-monitor" in args
    if cmd == "collect":
        cmd_collect(cfg)
    elif cmd == "monitor":
        base = storage.load_latest_baseline()
        posts, status = bm.check_bloggers(cfg, base)
        print(f"博主状态：{status}，新增 {len(posts)} 条")
    elif cmd == "verify":
        cmd_verify(cfg, skip_monitor=skip_monitor)
    elif cmd == "report":
        base = storage.load_latest_baseline()
        verified = vf.verify_all(storage.load_posts(), base, cfg)
        rep = rp.save_report(verified, base, cfg)
        print(f"报告：{rep.get('md_path')}")
    elif cmd == "run":
        cmd_collect(cfg)
        cmd_verify(cfg)
    elif cmd == "serve":
        cmd_serve(cfg)
    elif cmd == "schedule":
        cmd_schedule(cfg)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
