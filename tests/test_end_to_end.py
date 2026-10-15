"""端到端冒烟测试：不配置 API 也能完整跑通
「建任务 → 导数据 → 规则初筛 → 人工审核 → 导出」。
"""

from __future__ import annotations

import csv
import json

from src import export_service, rule_engine, snapshot
from src.data_loader import DEMO_TASK, load_demo_dataset
from src.models import REVIEW_STATUS_LABELS, ReviewDecision, RuleConfig


def test_full_journey_without_api(tmp_path) -> None:
    # 1) 任务配置
    task = DEMO_TASK
    config = RuleConfig()

    # 2) 加载演示数据
    dataset = load_demo_dataset()
    assert len(dataset.creators) >= 30
    assert not dataset.report.has_blocking_error

    # 3) 规则初筛
    assessments = rule_engine.evaluate_dataset(dataset.creators, task, config)
    counts = rule_engine.summarize_assessments(assessments)
    assert counts["total"] == len(dataset.creators)
    assert counts["qualified"] > 0, "演示数据里应该有通过硬性条件的候选"
    assert counts["excluded"] > 0, "演示数据里应该有被硬性条件排除的候选"
    assert counts["needs_review"] > 0, "演示数据里应该有因数据缺失而无法判定的候选"

    # 排名只在通过硬性条件的候选之间产生，且可复现
    qualified = [a for a in assessments if a.group == "qualified"]
    assert [a.rank for a in qualified] == list(range(1, len(qualified) + 1))
    again = rule_engine.evaluate_dataset(dataset.creators, task, config)
    assert [a.creator_id for a in again] == [a.creator_id for a in assessments]
    assert [a.weighted_score for a in again] == [a.weighted_score for a in assessments]

    # 4) 快照落盘（记录数据/任务/规则版本）
    run_id = snapshot.new_run_id()
    meta = snapshot.build_meta(
        run_id=run_id,
        task=task,
        config=config,
        creators=dataset.creators,
        dataset_source=dataset.source_name,
        data_origin=dataset.origin,
    )
    run_dir = snapshot.write_run(meta, runs_dir=tmp_path)
    saved = snapshot.read_run(run_dir)
    assert saved["data_hash"] and saved["task_hash"] and saved["rule_config_hash"]
    assert saved["data_row_count"] == len(dataset.creators)
    assert snapshot.staleness_reasons(
        saved,
        {
            "data_hash": saved["data_hash"],
            "task_hash": saved["task_hash"],
            "rule_config_hash": saved["rule_config_hash"],
            "prompt_version": None,
            "model_requested": None,
        },
    ) == []

    # 5) 人工审核（不覆盖规则结果）
    first = qualified[0]
    decisions = {
        first.creator_id: ReviewDecision(
            creator_id=first.creator_id, status="contact", note="人工核对通过"
        )
    }
    snapshot.write_run(
        saved,
        decisions={k: v.model_dump() for k, v in decisions.items()},
        runs_dir=tmp_path,
    )

    # 6) 导出（结果 CSV + 元数据 JSON，保留全部记录）
    rows = export_service.build_export_rows(assessments, dataset.creators, decisions)
    assert len(rows) == len(assessments)
    csv_path, meta_path = export_service.export_to_dir(rows, saved, tmp_path / "exports")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == len(assessments)
    assert {r["creator_id"] for r in exported} == {c.creator_id for c in dataset.creators}
    exported_first = [r for r in exported if r["creator_id"] == first.creator_id][0]
    assert exported_first["review_status"] == REVIEW_STATUS_LABELS["contact"]
    assert exported_first["review_note"] == "人工核对通过"
    assert exported_first["rule_reasons"], "导出必须带判定依据"
    assert exported_first["ai_status"] == "未运行", "没跑过 AI 就如实写未运行，不得伪造"

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert metadata["data_hash"] == saved["data_hash"]
    assert metadata["rule_version"] == config.version
    assert metadata["exported_row_count"] == len(rows)


def test_api_missing_does_not_block_rules() -> None:
    """没有 API Key 时，规则链路必须完整可跑（对应原指令 §八 第 13 项）。"""
    dataset = load_demo_dataset()
    assessments = rule_engine.evaluate_dataset(dataset.creators, DEMO_TASK, RuleConfig())
    assert len(assessments) == len(dataset.creators)
    assert all(a.weighted_score is not None or a.group == "excluded" for a in assessments)
