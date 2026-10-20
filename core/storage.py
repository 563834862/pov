"""数据存取与路径管理。"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # pov/
DATA = ROOT / "data"
BASELINES = DATA / "baselines"
POSTS_DIR = DATA / "posts"
REPORTS = DATA / "reports"
INPUTS = DATA / "inputs"
LOGS = ROOT / "logs"

for d in (BASELINES, POSTS_DIR, REPORTS, INPUTS, LOGS):
    d.mkdir(parents=True, exist_ok=True)

LATEST_BASELINE = BASELINES / "baseline-latest.json"
POSTS_FILE = POSTS_DIR / "posts.json"
STATE_FILE = DATA / "state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def load_config() -> dict:
    import yaml
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_baseline(analyzed: dict) -> Path:
    """保存分析后的基线快照，并更新 latest。"""
    ts = now_stamp()
    dst = BASELINES / f"baseline-{ts}.json"
    write_json(dst, analyzed)
    shutil.copyfile(dst, LATEST_BASELINE)
    return dst


def load_latest_baseline():
    return read_json(LATEST_BASELINE)


def load_posts() -> list:
    return read_json(POSTS_FILE, default=[]) or []


def save_posts(posts: list):
    write_json(POSTS_FILE, posts)


def load_state() -> dict:
    return read_json(STATE_FILE, default={
        "last_collect_at": None,
        "last_verify_at": None,
        "collect_status": {},
        "running": None,
    }) or {}


def save_state(state: dict):
    write_json(STATE_FILE, state)


def log(msg: str):
    line = f"[{now_iso()}] {msg}"
    with open(LOGS / "pov.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)
