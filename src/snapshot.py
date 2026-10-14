"""运行快照与版本管理。

每次分析生成一个 run 目录，记录当时使用的数据/任务/规则/Prompt/模型版本，
用于回答「这份结果是什么时候、基于什么配置算出来的」以及判断结果是否已过期。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from pathlib import Path

from .models import CreatorRecord, MarketingTask, RuleConfig

APP_VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
RUNS_DIR = RUNTIME_DIR / "runs"


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def canonical_creators_text(creators: list[CreatorRecord]) -> str:
    """规范化达人数据：字段名排序、去空白、统一小写、按 creator_id 排序。"""
    records = []
    for creator in sorted(creators, key=lambda c: c.creator_id):
        payload = {}
        for key, value in creator.model_dump().items():
            if key in {"missing_fields", "parse_warnings"}:
                continue
            if isinstance(value, str):
                payload[key] = value.strip().lower()
            elif isinstance(value, list):
                payload[key] = sorted(str(v).strip().lower() for v in value)
            else:
                payload[key] = value
        records.append(payload)
    return json.dumps(records, sort_keys=True, ensure_ascii=False)


def data_hash(creators: list[CreatorRecord]) -> str:
    return _short_hash(canonical_creators_text(creators))


def task_hash(task: MarketingTask) -> str:
    return _short_hash(json.dumps(task.model_dump(), sort_keys=True, ensure_ascii=False))


def rule_config_hash(config: RuleConfig) -> str:
    return _short_hash(json.dumps(config.model_dump(), sort_keys=True, ensure_ascii=False))


def new_run_id(moment: datetime | None = None) -> str:
    moment = moment or datetime.now()
    return moment.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def build_meta(
    run_id: str,
    task: MarketingTask,
    config: RuleConfig,
    creators: list[CreatorRecord],
    dataset_source: str,
    data_origin: str,
    prompt_version: str | None = None,
    model_requested: str | None = None,
    model_returned: str | None = None,
    analysis_top_n: int | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": APP_VERSION,
        "data_source": dataset_source,
        "data_origin": data_origin,
        "data_hash": data_hash(creators),
        "data_row_count": len(creators),
        "task_config": task.model_dump(mode="json"),
        "task_hash": task_hash(task),
        "rule_version": config.version,
        "rule_config": config.model_dump(mode="json"),
        "rule_config_hash": rule_config_hash(config),
        "prompt_version": prompt_version,
        "model_requested": model_requested,
        "model_returned": model_returned,
        "analysis_top_n": analysis_top_n,
        "ai_enabled": model_requested is not None,
    }


def write_run(
    meta: dict,
    rule_rows: list[dict] | None = None,
    decisions: dict | None = None,
    runs_dir: Path | None = None,
) -> Path:
    run_dir = (runs_dir or RUNS_DIR) / meta["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if rule_rows is not None:
        csv_text = _rows_to_csv(rule_rows)
        (run_dir / "rule_results.csv").write_text(csv_text, encoding="utf-8-sig")
    if decisions is not None:
        (run_dir / "decisions.json").write_text(
            json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return run_dir


def _rows_to_csv(rows: list[dict]) -> str:
    import csv
    import io

    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def read_run(run_dir: Path) -> dict:
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def list_runs(runs_dir: Path | None = None) -> list[dict]:
    root = runs_dir or RUNS_DIR
    if not root.exists():
        return []
    runs = [read_run(p) for p in sorted(root.iterdir()) if p.is_dir()]
    return [r for r in runs if r]


def staleness_reasons(meta: dict, current: dict) -> list[str]:
    """比较快照与当前配置，返回不一致的字段说明（为空表示没过期）。"""
    reasons: list[str] = []
    labels = {
        "data_hash": "达人数据已变化",
        "task_hash": "营销任务配置已修改",
        "rule_config_hash": "规则配置已修改",
        "prompt_version": "Prompt 版本不同",
        "model_requested": "使用的模型不同",
    }
    for key, label in labels.items():
        if meta.get(key) != current.get(key):
            reasons.append(f"{label}（快照 {meta.get(key)} → 当前 {current.get(key)}）")
    return reasons
