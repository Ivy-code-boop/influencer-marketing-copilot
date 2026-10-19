"""离线评估模块测试：案例集完整性、判定逻辑、报告口径。"""

from __future__ import annotations

import json

from src import evaluation
from src.ai_client import ChatResult
from src.models import AIAnalysisRecord


class AlwaysRecommendClient:
    """测试用假客户端：对每个案例都返回「三维度匹配」。"""

    model = "fake-eval"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> ChatResult:
        self.calls += 1
        start = user_prompt.rfind('{\n  "task"')
        payload, _ = json.JSONDecoder().raw_decode(user_prompt[start:])
        creator = payload["creators"][0]
        data = {
            "creator_id": creator["creator_id"],
            "analysis_status": "ok",
            "content_relevance": {"verdict": "strong", "reason": "匹配", "evidence": []},
            "goal_fit": {"verdict": "strong", "reason": "匹配", "evidence": []},
            "audience_fit": {"verdict": "moderate", "reason": "部分匹配", "evidence": []},
            "recommendation_reasons": ["测试用"],
            "mismatches": [],
            "risks": [],
            "missing_info": [],
            "human_checks": [],
            "data_conflicts": [],
            "confidence": "medium",
        }
        return ChatResult(
            text=json.dumps(data, ensure_ascii=False), data=data, model=self.model, attempts=1
        )


def test_case_set_is_complete_and_labelled() -> None:
    payload = evaluation.load_cases()
    assert len(payload["cases"]) == 12
    assert "AI 起草" in payload["label_source"]
    for case in payload["cases"]:
        assert case["expected"] in {"recommend", "not_recommend", "insufficient"}
        assert case["creator"]["creator_id"] == case["creator"]["creator_id"].strip()
        assert case["reference_note"], "每个案例都必须写清参考判定的理由"


def test_rule_only_evaluation_matches_known_baseline() -> None:
    report = evaluation.run_evaluation()
    assert report["case_count"] == 12
    assert report["ai_run"] is False
    decisions = {r["case_id"]: r["rules_decision"] for r in report["rows"]}
    # 规则能处理的部分
    assert decisions["E01"] == "recommend"
    assert decisions["E03"] == "not_recommend"
    assert decisions["E04"] == "insufficient"
    assert decisions["E09"] == "insufficient"
    assert decisions["E10"] == "insufficient"
    # 规则结构上看不到语义的三个案例
    assert decisions["E05"] == "recommend"      # 内容风险
    assert decisions["E06"] == "recommend"      # 目标不一致
    assert decisions["E07"] == "recommend"      # 指标好、语义差
    assert report["summary"]["rules_correct"] == 9


def test_offline_report_states_ai_not_run_and_small_sample() -> None:
    text = evaluation.render_markdown(evaluation.run_evaluation())
    assert "AI：未运行" in text
    assert "12 个案例" in text
    assert "不能当作行业准确率" in text


def test_ai_evaluation_reports_incremental_and_new_errors() -> None:
    client = AlwaysRecommendClient()
    report = evaluation.run_evaluation(client=client)
    assert client.calls == 12
    assert report["ai_run"] is True
    summary = report["summary"]
    assert "ai_correct" in summary
    assert "incremental" in summary and "new_errors" in summary
    # 假客户端一律推荐：参考为「不推荐」的案例里，规则判对的会成为新增错误
    assert summary["new_errors"], "必须把 AI 引入的错误单独统计出来"
    assert summary["ai_failed"] == []


def test_decide_by_ai_handles_failure_without_guessing() -> None:
    record = AIAnalysisRecord(creator_id="x", status="failed", error_kind="timeout")
    assert evaluation.decide_by_ai(record) == "ai_failed"
    assert evaluation.decide_by_ai(None) == "not_run"
