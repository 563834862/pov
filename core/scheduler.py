"""定时调度：采集/验证自动周期运行，无需手动点击。

三种使用方式：
  1. 看板内嵌（默认）：`python pov.py serve` 启动时自动开启，
     按 config.yaml 的 schedule 段周期执行（看板进程停则停）；
  2. 独立常驻：`python pov.py schedule` —— 不开看板，纯后台循环，Ctrl+C 停止；
  3. Windows 计划任务（最稳，推荐，见 README「自动调度」）：
       schtasks /Create /TN "POV-Daily-Run"  /SC DAILY /ST 08:00 /TR "<python> pov.py run"
       schtasks /Create /TN "POV-Dashboard"  /SC ONLOGON          /TR "<pythonw> pov.py serve"
     每天定点跑全流程 + 开机自动起看板；看板内嵌调度覆盖白天周期性刷新。

防重入：collect / verify 各持 data/.lock-<name> 文件锁，
看板线程与计划任务同时触发时只有一个能跑；进程崩溃残留的锁 45 分钟后自动破除，
不会把后续调度永久堵死。
"""
import os
import threading
import time
from datetime import datetime, timezone

from core import storage
from core.baseline import build_baseline
from core.blogger_monitor import check_bloggers
from core.verify import verify_all
from core.report import save_report
from core import evolution

DEFAULTS = {
    "enabled": True,
    "collect_interval_hours": 6,
    "verify_interval_hours": 2,
    "run_on_start": True,
    "start_delay_seconds": 30,
    "verify_delay_after_collect_seconds": 60,
}

_LOCK_STALE_SECONDS = 45 * 60   # 崩溃残留锁的自动破除时间


# ---------------- 文件锁（跨进程防重入） ----------------

def _acquire_lock(name: str):
    path = storage.DATA / f".lock-{name}"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return path
        except FileExistsError:
            try:   # 陈旧锁（上次进程崩溃残留）→ 破除重试一次
                if time.time() - path.stat().st_mtime > _LOCK_STALE_SECONDS:
                    storage.log(f"[scheduler] 破除陈旧锁 {path.name}")
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return None
        except OSError as e:
            storage.log(f"[scheduler] 加锁失败 {name}: {e}")
            return None
    return None


def _release_lock(path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------- 任务（看板按钮、CLI、调度线程、计划任务共用） ----------------

def job_collect(cfg) -> dict:
    """采集舆情基线。返回 build_baseline 结果；被锁挡回时返回 {"skipped": True}。"""
    lock = _acquire_lock("collect")
    if not lock:
        return {"skipped": True, "reason": "采集锁被占用（另一进程正在采集）"}
    try:
        r = build_baseline(cfg)
        st = storage.load_state()
        st["last_collect_at"] = storage.now_iso()
        st["collect_status"] = r.get("source_status") or {}
        storage.save_state(st)
        return r
    finally:
        _release_lock(lock)


def job_verify(cfg, run_monitor=True) -> dict:
    """博主监控 + 验证 + 报告。博主监控失败不阻塞验证（用已采集数据继续）。"""
    lock = _acquire_lock("verify")
    if not lock:
        return {"skipped": True, "reason": "验证锁被占用（另一进程正在验证）"}
    try:
        base = storage.load_latest_baseline()
        if not base or not base.get("ok"):
            return {"error": "尚无有效基线，跳过验证（先跑一次采集）"}
        if run_monitor:
            try:
                check_bloggers(cfg, base)
            except Exception as e:
                storage.log(f"[scheduler] 博主监控失败，继续用已采集数据验证: {e}")
        verified = verify_all(storage.load_posts(), base, cfg)
        storage.save_posts(verified)
        # 自我进化闭环：博主内容反推采集缺口 → 自动补词 → 闭环验证 → 回写帖子
        evo = evolution.run_cycle(verified, base, cfg)
        storage.save_posts(verified)      # 带回写后的 gap 标记再存一次
        rep = save_report(verified, base, cfg, evo)
        st = storage.load_state()
        st["last_verify_at"] = storage.now_iso()
        st["last_evolution"] = evo
        storage.save_state(st)
        return rep
    finally:
        _release_lock(lock)


# ---------------- 调度线程 ----------------

class _Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = None            # 正在执行的任务名
        self.collect_count = 0
        self.verify_count = 0
        self.last_error = ""
        self.next_collect_at = None    # time.time() 时间戳
        self.next_verify_at = None


_ST = _Status()
_stop_evt = threading.Event()
_thread: threading.Thread | None = None


def _iso(ts) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def status() -> dict:
    st = storage.load_state()
    with _ST.lock:
        return {
            "running": _ST.running,
            "collect_count": _ST.collect_count,
            "verify_count": _ST.verify_count,
            "last_error": _ST.last_error,
            "next_collect_at": _iso(_ST.next_collect_at),
            "next_verify_at": _iso(_ST.next_verify_at),
            "last_collect_at": st.get("last_collect_at"),
            "last_verify_at": st.get("last_verify_at"),
        }


def _run_job(name: str, fn) -> None:
    with _ST.lock:
        _ST.running = name
    try:
        r = fn()
        if isinstance(r, dict) and r.get("error"):
            with _ST.lock:
                _ST.last_error = f"{name}: {r['error']}"
            storage.log(f"[scheduler] {name} 失败: {r['error']}")
        elif isinstance(r, dict) and r.get("skipped"):
            storage.log(f"[scheduler] {name} 跳过: {r.get('reason')}")
        else:
            with _ST.lock:
                if name == "collect":
                    _ST.collect_count += 1
                else:
                    _ST.verify_count += 1
                _ST.last_error = ""
            storage.log(f"[scheduler] {name} 完成")
    except Exception as e:
        with _ST.lock:
            _ST.last_error = f"{name}: {e}"
        storage.log(f"[scheduler] {name} 异常: {e}")
    finally:
        with _ST.lock:
            _ST.running = None


def _loop(cfg) -> None:
    sc = {**DEFAULTS, **(cfg.get("schedule") or {})}
    ci = max(5.0, float(sc["collect_interval_hours"])) * 3600
    vi = max(5.0, float(sc["verify_interval_hours"])) * 3600
    vd = float(sc["verify_delay_after_collect_seconds"])
    delay = float(sc["start_delay_seconds"]) if sc.get("run_on_start") else ci
    next_collect = time.time() + delay
    next_verify = next_collect + vd
    storage.log(f"[scheduler] 调度启动：采集每 {sc['collect_interval_hours']}h / "
                f"验证每 {sc['verify_interval_hours']}h，"
                f"{'启动后 ' + str(int(delay)) + 's 首跑' if sc.get('run_on_start') else '等待首个周期'}")
    while not _stop_evt.wait(5):
        now = time.time()
        if not sc.get("enabled", True):
            with _ST.lock:
                _ST.next_collect_at = _ST.next_verify_at = None
            continue
        if now >= next_collect:
            _run_job("collect", lambda: job_collect(cfg))
            now = time.time()
            next_collect = now + ci
            next_verify = min(next_verify, now + vd)   # 采完尽快验证一次
        if now >= next_verify:
            _run_job("verify", lambda: job_verify(cfg))
            next_verify = time.time() + vi
        with _ST.lock:
            _ST.next_collect_at = next_collect
            _ST.next_verify_at = next_verify


def start(cfg) -> bool:
    """启动调度守护线程（看板 serve 或 pov.py schedule 调用）。已配置关闭则返回 False。"""
    global _thread
    sc = cfg.get("schedule") or {}
    if not sc.get("enabled", True):
        storage.log("[scheduler] 配置为关闭，不启动自动调度")
        return False
    if _thread and _thread.is_alive():
        return False
    _stop_evt.clear()
    _thread = threading.Thread(target=_loop, args=(cfg,), daemon=True, name="pov-scheduler")
    _thread.start()
    return True


def stop() -> None:
    _stop_evt.set()


def summary(cfg) -> str:
    sc = {**DEFAULTS, **(cfg.get("schedule") or {})}
    if not sc.get("enabled", True):
        return "自动调度：已关闭（手动触发）"
    return (f"自动调度：开 · 采集每 {sc['collect_interval_hours']}h / "
            f"验证每 {sc['verify_interval_hours']}h")
