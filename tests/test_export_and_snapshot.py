"""导出与快照测试：导出不丢记录、带版本信息、过期判定正确。"""

from __future__ import annotations

import json

from src import export_service, rule_engine, snapshot
from src.models import MarketingTask, ReviewDecision, RuleConfig
from tests.test_rule_engine import make_creator, make_task


def sample_assessments():
    creators = [
        make_creator(creator_id="best"),
        make_creator(creator_id="unknown", follower_count=None),
        make_creator(creator_id="out", follower_count=1_000),
    ]
    return creators, rule_engine.evaluate_dataset(creators, make_task(), RuleConfig())


def test_export_keeps_every_record() -> None:
    creators, assessments = sample_assessments()
    rows = export_service.build_export_rows(assessments, creators, {})
    assert len(rows) == len(assessments)
    assert {r["creator_id"] for r in rows} == {"best", "unknown", "out"}


def test_export_marks_ai_as_not_run_when_never_executed() -> None:
    creators, assessments = sample_assessments()
    rows = export_service.build_export_rows(assessments, creators, {})
    assert all(r["ai_status"] == "未运行" for r in rows)
    assert all(r["ai_summary"] == "" for r in rows)


def test_export_carries_ai_columns_when_ai_ran() -> None:
    from src.models import AIAnalysis, AIAnalysisRecord

    creators, assessments = sample_assessments()
    analysis = AIAnalysis.model_validate(
        {
            "creator_id": "best",
            "analysis_status": "ok",
            "content_relevance": {"verdict": "strong", "reason": "内容贴合", "evidence": []},
            "goal_fit": {"verdict": "moderate", "reason": "适合种草", "evidence": []},
            "audience_fit": {"verdict": "unknown", "reason": "缺受众描述", "evidence": []},
            "recommendation_reasons": ["内容主题贴合"],
            "risks": [{"type": "content_risk", "detail": "风格偏猎奇", "evidence": []}],
            "missing_info": ["audience_description"],
            "human_checks": ["确认报价"],
            "confidence": "medium",
        }
    )
    record = AIAnalysisRecord(
        creator_id="best",
        status="ok",
        analysis=analysis,
        model_returned="deepseek-chat",
        attempts=1,
        evidence_problems=["受众匹配：引用了空字段「audience_description」"],
    )
    rows = export_service.build_export_rows(
        assessments, creators, {}, {"best": record}
    )
    row = [r for r in rows if r["creator_id"] == "best"][0]
    assert row["ai_status"] == "已完成"
    assert row["ai_content_relevance"] == "明确匹配"
    assert row["ai_audience_fit"] == "信息不足"
    assert "content_risk" in row["ai_risks"]
    assert "audience_description" in row["ai_missing_info"]
    assert row["ai_evidence_problems"], "证据问题必须原样带进导出"
    untouched = [r for r in rows if r["creator_id"] == "out"][0]
    assert untouched["ai_status"] == "未运行"


def test_export_includes_review_state_and_note() -> None:
    creators, assessments = sample_assessments()
    decisions = {"best": ReviewDecision(creator_id="best", status="contact", note="已核对数据")}
    rows = export_service.build_export_rows(assessments, creators, decisions)
    row = [r for r in rows if r["creator_id"] == "best"][0]
    assert row["review_status"] == "建议联系"
    assert row["review_note"] == "已核对数据"


def test_csv_has_bom_and_meta_has_version_info(tmp_path=None) -> None:
    creators, assessments = sample_assessments()
    rows = export_service.build_export_rows(assessments, creators, {})
    text = export_service.rows_to_csv_text(rows)
    assert text.splitlines()[0].startswith("creator_id")
    task = make_task()
    config = RuleConfig()
    meta = snapshot.build_meta(
        run_id="20261014-000000-abcd",
        task=task,
        config=config,
        creators=creators,
        dataset_source="demo_creators.csv",
        data_origin="demo",
    )
    export_meta = export_service.build_export_meta(meta, rows)
    for key in ("data_source", "analyzed_at", "rule_version", "data_hash", "exported_row_count"):
        assert export_meta[key] is not None
    assert export_meta["exported_row_count"] == 3
    assert "未做删除" in export_meta["notes"]


def test_export_writes_two_files(tmp_path) -> None:
    creators, assessments = sample_assessments()
    rows = export_service.build_export_rows(assessments, creators, {})
    meta = snapshot.build_meta(
        run_id="r1",
        task=make_task(),
        config=RuleConfig(),
        creators=creators,
        dataset_source="demo",
        data_origin="demo",
    )
    csv_path, meta_path = export_service.export_to_dir(rows, meta, tmp_path)
    assert csv_path.exists() and meta_path.exists()
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 需要 UTF-8 BOM 以免 Excel 中文乱码"
    assert json.loads(meta_path.read_text(encoding="utf-8"))["run_id"] == "r1"


def test_snapshot_writes_meta_and_detects_staleness(tmp_path) -> None:
    creators, assessments = sample_assessments()
    task = make_task()
    config = RuleConfig()
    meta = snapshot.build_meta(
        run_id="run-1",
        task=task,
        config=config,
        creators=creators,
        dataset_source="demo",
        data_origin="demo",
    )
    rows = export_service.build_export_rows(assessments, creators, {})
    run_dir = snapshot.write_run(meta, rule_rows=rows, runs_dir=tmp_path)
    assert (run_dir / "meta.json").exists()
    assert (run_dir / "rule_results.csv").exists()
    assert snapshot.read_run(run_dir)["run_id"] == "run-1"

    same = {
        "data_hash": snapshot.data_hash(creators),
        "task_hash": snapshot.task_hash(task),
        "rule_config_hash": snapshot.rule_config_hash(config),
        "prompt_version": None,
        "model_requested": None,
    }
    assert snapshot.staleness_reasons(meta, same) == []

    changed_creators = creators[:-1]
    changed = dict(same, data_hash=snapshot.data_hash(changed_creators))
    assert any("达人数据已变化" in r for r in snapshot.staleness_reasons(meta, changed))

    new_config = RuleConfig(weights={**config.weights, "engagement": 0.5})
    changed2 = dict(same, rule_config_hash=snapshot.rule_config_hash(new_config))
    assert any("规则配置已修改" in r for r in snapshot.staleness_reasons(meta, changed2))

    changed3 = dict(same, task_hash=snapshot.task_hash(MarketingTask(**{**task.model_dump(), "target_market": "美国"})))
    assert any("营销任务配置已修改" in r for r in snapshot.staleness_reasons(meta, changed3))


def test_data_hash_is_stable_and_sensitive() -> None:
    creators = [make_creator(creator_id="a"), make_creator(creator_id="b")]
    assert snapshot.data_hash(creators) == snapshot.data_hash(list(creators))
    changed = [make_creator(creator_id="a", follower_count=123), make_creator(creator_id="b")]
    assert snapshot.data_hash(creators) != snapshot.data_hash(changed)
