"""POV Web 看板（标准库 http.server，零外部依赖）。

路由：
  GET  /                 看板页面
  GET  /static/*         静态资源
  GET  /api/state        全量状态（基线/关注点/验证结果/画像/任务状态）
  POST /api/collect      触发基线采集（后台执行）
  POST /api/verify       触发监控+验证（后台执行）
  POST /api/report       重新生成报告（后台执行）
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from core import storage, scheduler
from core.baseline import build_baseline
from core.blogger_monitor import check_bloggers
from core.timeline import blogger_response_speed
from core.verify import (verify_all, opinion_focus, opinion_signals,
                         blogger_hot_topics, blogger_profiles)
from core.report import save_report

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
POOL = ThreadPoolExecutor(max_workers=1)
JOB = {"running": None, "message": "", "finished_at": None}


def _run_job(name, fn):
    JOB["running"] = name
    JOB["message"] = f"{name} 执行中…"
    JOB["finished_at"] = None
    try:
        result = fn()
        JOB["message"] = f"{name} 完成"
        return result
    except Exception as e:
        JOB["message"] = f"{name} 失败：{e}"
        storage.log(f"任务 {name} 失败: {e}")
        return None
    finally:
        JOB["finished_at"] = storage.now_iso()
        JOB["running"] = None


def build_state(cfg) -> dict:
    base = storage.load_latest_baseline()
    posts = storage.load_posts()
    state = storage.load_state()
    focus = opinion_focus(base, top_n=10) if base else []
    hot_topics = blogger_hot_topics(posts, top_n=10) if posts else []
    profiles = blogger_profiles(posts) if posts else []
    return {
        "system": {
            "name": cfg.get("system", {}).get("name", "POV"),
            "version": cfg.get("system", {}).get("version", ""),
            "keywords": cfg.get("keywords") or [],
            "bloggers": [b.get("name") for b in cfg.get("bloggers") or []],
        },
        "baseline": {
            "ok": bool(base and base.get("ok")),
            "health_score": (base or {}).get("health_score"),
            "collected_at": (base or {}).get("collected_at"),
            "source_status": (base or {}).get("source_status") or {},
            "item_count": len((base or {}).get("items") or []),
            "event_count": len((base or {}).get("pov_events") or []),
            "focus": focus,
            "signals": opinion_signals(base, top_n=12) if base else [],
            "source_catalog": (base or {}).get("source_catalog") or {},
        },
        "hot_topics": hot_topics,
        "posts": posts,
        "profiles": profiles,
        "speed": blogger_response_speed(posts, (base or {}).get("pov_events") or []) if posts else [],
        "job": {"running": JOB["running"], "message": JOB["message"],
                "finished_at": JOB["finished_at"]},
        "scheduler": {**scheduler.status(),
                      "enabled": (cfg.get("schedule") or {}).get("enabled", True),
                      "collect_interval_hours": (cfg.get("schedule") or {}).get("collect_interval_hours", 6),
                      "verify_interval_hours": (cfg.get("schedule") or {}).get("verify_interval_hours", 2)},
        "last_collect_at": state.get("last_collect_at"),
        "last_verify_at": state.get("last_verify_at"),
    }


def _event_followers(posts: list, ev_id: str, items_meta: list) -> list:
    """某事件的跟进博主列表（含滞后、判定、作品链接）。"""
    from core.timeline import _post_ref, _follower_entry, fmt_lag
    out = []
    for p in posts:
        me = (p.get("verification") or {}).get("matched_event") or {}
        if me.get("id") != ev_id:
            continue
        ref_dt, ref_title, basis = _post_ref(p, items_meta or [])
        entry = _follower_entry(p, ref_dt, ref_title, basis)
        out.append(entry)
    out.sort(key=lambda f: f.get("published_at") or "")
    return out


def _event_list(base: dict, posts: list, start: str, end: str,
                q: str, page: int, size: int) -> dict:
    """舆情事件库：按日期过滤 + 关键词 + 分页（标题/时间为重点字段）。"""
    follower_cnt = {}
    for p in posts:
        me = (p.get("verification") or {}).get("matched_event") or {}
        eid = me.get("id")
        if eid:
            follower_cnt[eid] = follower_cnt.get(eid, 0) + 1

    rows = []
    for ev in (base or {}).get("pov_events") or []:
        fs = (ev.get("first_seen") or "")[:10]
        if start and fs and fs < start:
            continue
        if end and fs and fs > end:
            continue
        if q and q not in ((ev.get("title") or "") + (ev.get("topic") or "")):
            continue
        rows.append({
            "id": ev.get("id"), "topic": ev.get("topic") or "",
            "title": ev.get("title") or "", "phase": ev.get("phase") or "未知",
            "count": ev.get("count") or 0,
            "platforms": ev.get("platforms") or [],
            "first_seen": ev.get("first_seen") or "",
            "last_seen": ev.get("last_seen") or "",
            "avg_eng": ev.get("avg_eng") or 0,
            "follower_count": follower_cnt.get(ev.get("id"), 0),
        })
    rows.sort(key=lambda r: r["first_seen"], reverse=True)   # 最新舆情在前
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    return {"total": total, "page": page, "size": size, "pages": pages,
            "events": rows[(page - 1) * size: page * size]}


def _event_detail(base: dict, posts: list, ev_id: str) -> dict:
    """事件详情：舆情本体 + 报道列表 + 跟进博主作品（带链接）。"""
    ev = next((e for e in (base or {}).get("pov_events") or []
               if e.get("id") == ev_id), None)
    if not ev:
        return {"ok": False, "error": "事件不存在"}
    followers = _event_followers(posts, ev_id, ev.get("items_meta") or [])
    after = [f for f in followers if not f["early"]]
    return {
        "ok": True,
        "event": {
            "id": ev.get("id"), "topic": ev.get("topic") or "",
            "title": ev.get("title") or "", "phase": ev.get("phase") or "未知",
            "count": ev.get("count") or 0, "platforms": ev.get("platforms") or [],
            "first_seen": ev.get("first_seen") or "", "last_seen": ev.get("last_seen") or "",
            "avg_eng": ev.get("avg_eng") or 0, "max_eng": ev.get("max_eng") or 0,
            "topics": ev.get("topics") or [],
        },
        "reports": ev.get("items_meta") or [],
        "followers": followers,
        "after_count": len(after),
        "before_count": len(followers) - len(after),
    }


class Handler(BaseHTTPRequestHandler):
    cfg = None  # serve() 注入

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype):
        try:
            body = path.read_bytes()
        except OSError:
            self._json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(build_state(self.cfg))
        elif path == "/api/events":
            qs = parse_qs(urlparse(self.path).query)
            base = storage.load_latest_baseline()
            posts = storage.load_posts()
            self._json(_event_list(
                base, posts,
                start=(qs.get("start") or [""])[0],
                end=(qs.get("end") or [""])[0],
                q=(qs.get("q") or [""])[0].strip(),
                page=int((qs.get("page") or ["1"])[0]),
                size=int((qs.get("size") or ["10"])[0])))
        elif path == "/api/event":
            qs = parse_qs(urlparse(self.path).query)
            base = storage.load_latest_baseline()
            posts = storage.load_posts()
            self._json(_event_detail(base, posts, (qs.get("id") or [""])[0]))
        elif path.startswith("/static/"):
            p = STATIC_DIR / path[len("/static/"):]
            ctype = {".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                     ".html": "text/html; charset=utf-8"}.get(p.suffix, "application/octet-stream")
            if p.resolve().is_relative_to(STATIC_DIR.resolve()):
                self._file(p, ctype)
            else:
                self._json({"error": "forbidden"}, 403)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        cfg = self.cfg
        if path == "/api/collect":
            if JOB["running"]:
                self._json({"ok": False, "message": "已有任务在执行"}, 409)
                return

            def job():
                r = scheduler.job_collect(cfg)
                if r.get("skipped"):
                    return r
                st = storage.load_state()
                st["last_collect_at"] = storage.now_iso()
                st["collect_status"] = r.get("source_status") or {}
                storage.save_state(st)
                return r
            POOL.submit(_run_job, "collect", job)
            self._json({"ok": True, "message": "基线采集已启动"})
        elif path == "/api/verify":
            if JOB["running"]:
                self._json({"ok": False, "message": "已有任务在执行"}, 409)
                return

            def job():
                r = scheduler.job_verify(cfg)
                if r.get("skipped") or r.get("error"):
                    return r
                st = storage.load_state()
                st["last_verify_at"] = storage.now_iso()
                storage.save_state(st)
                return r
            POOL.submit(_run_job, "verify", job)
            self._json({"ok": True, "message": "验证已启动"})
        elif path == "/api/report":
            if JOB["running"]:
                self._json({"ok": False, "message": "已有任务在执行"}, 409)
                return

            def job():
                base = storage.load_latest_baseline()
                verified = verify_all(storage.load_posts(), base, cfg)
                return save_report(verified, base, cfg)
            POOL.submit(_run_job, "report", job)
            self._json({"ok": True, "message": "报告生成已启动"})
        else:
            self._json({"error": "not found"}, 404)


def serve(cfg):
    Handler.cfg = cfg
    host = (cfg.get("web") or {}).get("host", "127.0.0.1")
    port = int((cfg.get("web") or {}).get("port", 8765))
    server = ThreadingHTTPServer((host, port), Handler)
    storage.log(f"看板已启动: http://{host}:{port}")
    print(f"POV 看板: http://{host}:{port}  (Ctrl+C 停止)")
    if scheduler.start(cfg):
        print(scheduler.summary(cfg))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
