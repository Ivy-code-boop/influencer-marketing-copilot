"""AI 分析层测试：全部使用假客户端，不产生任何真实调用与费用。

对应原指令 §八 的第 6、7、8、9 项：合法结构、非法结构、超时/鉴权/限流、单条重试。
"""

from __future__ import annotations

import json

import pytest

from src import ai_analyzer, rule_engine
from src.ai_client import AIError, ChatResult, DeepSeekClient, classify_exception, scrub
from src.data_loader import DEMO_TASK, load_demo_dataset
from src.models import RuleConfig


class FakeClient:
    """按脚本回放的假客户端：返回预设结果或抛出预设错误。"""

    model = "fake-model-v1"

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls = 0
        self.last_user_prompt = ""

    def complete_json(self, system_prompt: str, user_prompt: str) -> ChatResult:
        self.calls += 1
        self.last_user_prompt = user_prompt
        if not self.script:
            raise AssertionError("假客户端被调用次数超过脚本长度")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        data = None if isinstance(item, str) else item
        return ChatResult(text=text, data=data, model=self.model, attempts=1)


@pytest.fixture(scope="module")
def demo():
    dataset = load_demo_dataset()
    assessments = rule_engine.evaluate_dataset(dataset.creators, DEMO_TASK, RuleConfig())
    by_id = {a.creator_id: a for a in assessments}
    creators = {c.creator_id: c for c in dataset.creators}
    creator_id = "de_lena_coffee"
    return {
        "dataset": dataset,
        "creators": creators,
        "assessments": by_id,
        "creator_id": creator_id,
        "pairs": [(c, by_id[c.creator_id]) for c in dataset.creators[:3]],
    }


def valid_payload(creator_id: str, **overrides) -> dict:
    payload = {
        "creator_id": creator_id,
        "analysis_status": "ok",
        "content_relevance": {
            "verdict": "strong",
            "reason": "内容主题与奶泡杯卖点高度相关",
            "evidence": [{"field": "sample_content_text", "quote": "30 秒手打奶泡"}],
        },
        "goal_fit": {
            "verdict": "moderate",
            "reason": "近期以教程为主，适合内容种草",
            "evidence": [
                {"field": "recent_content_summary", "quote": "柏林租房咖啡角日常与冷萃教程"}
            ],
        },
        "audience_fit": {
            "verdict": "strong",
            "reason": "受众与任务目标受众重合",
            "evidence": [{"field": "audience_description", "quote": "20-35 岁城市租房女性"}],
        },
        "recommendation_reasons": ["内容主题贴合", "受众重合"],
        "mismatches": [],
        "risks": [],
        "missing_info": [],
        "human_checks": ["确认报价与档期"],
        "data_conflicts": [],
        "confidence": "medium",
    }
    payload.update(overrides)
    return payload


def run_one(demo, script, **kwargs):
    client = FakeClient(script)
    creator = demo["creators"][demo["creator_id"]]
    assessment = demo["assessments"][demo["creator_id"]]
    record = ai_analyzer.analyze_one(
        creator, assessment, DEMO_TASK, client, template="模板 {{payload}}", **kwargs
    )
    return client, record


def test_valid_result_is_accepted_and_evidence_checked(demo) -> None:
    client, record = run_one(demo, [valid_payload(demo["creator_id"])])
    assert record.status == "ok"
    assert record.analysis is not None
    assert record.analysis.content_relevance.verdict == "strong"
    assert record.evidence_problems == []
    assert record.numeric_conflicts == []
    assert client.calls == 1


def test_prompt_payload_contains_data_but_not_api_key(demo) -> None:
    client, record = run_one(demo, [valid_payload(demo["creator_id"])])
    assert "30 秒手打奶泡" in client.last_user_prompt
    assert "sk-" not in client.last_user_prompt
    assert "weighted_score" not in client.last_user_prompt, "不应把规则总分喂给模型，避免锚定"


def test_fake_evidence_is_flagged_not_silently_accepted(demo) -> None:
    payload = valid_payload(
        demo["creator_id"],
        content_relevance={
            "verdict": "strong",
            "reason": "看起来很搭",
            "evidence": [{"field": "sample_content_text", "quote": "这句原文并不存在"}],
        },
    )
    _, record = run_one(demo, [payload])
    assert record.status == "ok", "证据问题不应把结论直接作废"
    assert record.evidence_problems, "必须标记证据无法核对"
    assert record.error_kind == "evidence"


def test_evidence_from_unknown_field_is_flagged(demo) -> None:
    payload = valid_payload(
        demo["creator_id"],
        content_relevance={
            "verdict": "moderate",
            "reason": "…",
            "evidence": [{"field": "detect_brain", "quote": "x"}],
        },
    )
    _, record = run_one(demo, [payload])
    assert any("不存在的字段" in p for p in record.evidence_problems)


def test_invented_number_is_flagged_as_conflict(demo) -> None:
    payload = valid_payload(
        demo["creator_id"],
        recommendation_reasons=["该达人粉丝量为 999000，互动表现优秀"],
    )
    _, record = run_one(demo, [payload])
    assert record.numeric_conflicts, "编造的数字必须被标记出来"


def test_invalid_json_becomes_failed_record(demo) -> None:
    client = FakeClient([AIError("format", "模型返回的不是合法 JSON")])
    record = ai_analyzer.analyze_one(
        demo["creators"][demo["creator_id"]],
        demo["assessments"][demo["creator_id"]],
        DEMO_TASK,
        client,
        template="t {{payload}}",
    )
    assert record.status == "failed"
    assert record.analysis is None, "失败时不得给出伪造结论"
    assert record.error_kind == "format"


def test_missing_field_becomes_validation_failure(demo) -> None:
    payload = valid_payload(demo["creator_id"])
    payload.pop("goal_fit")
    client = FakeClient([AIError("validation", "结构校验失败：goal_fit Field required")])
    record = ai_analyzer.analyze_one(
        demo["creators"][demo["creator_id"]],
        demo["assessments"][demo["creator_id"]],
        DEMO_TASK,
        client,
        template="t {{payload}}",
    )
    assert record.status == "failed"
    assert record.error_kind == "validation"


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("timeout", "请求超时"),
        ("auth", "鉴权失败"),
        ("rate_limit", "触发限流"),
    ],
)
def test_api_failures_are_classified_and_never_faked(demo, kind, message) -> None:
    client = FakeClient([AIError(kind, message)])
    record = ai_analyzer.analyze_one(
        demo["creators"][demo["creator_id"]],
        demo["assessments"][demo["creator_id"]],
        DEMO_TASK,
        client,
        template="t {{payload}}",
    )
    assert record.status == "failed" and record.analysis is None
    assert record.error_kind == kind
    assert record.error_label


def test_cache_hit_avoids_second_call(demo, tmp_path) -> None:
    cache = ai_analyzer.AnalysisCache(tmp_path / "cache.json")
    client = FakeClient([valid_payload(demo["creator_id"])])
    creator = demo["creators"][demo["creator_id"]]
    assessment = demo["assessments"][demo["creator_id"]]
    first = ai_analyzer.analyze_one(
        creator, assessment, DEMO_TASK, client, template="t {{payload}}",
        cache=cache, data_hash="dh", task_hash_value="th",
    )
    assert first.from_cache is False and client.calls == 1

    second = ai_analyzer.analyze_one(
        creator, assessment, DEMO_TASK, client, template="t {{payload}}",
        cache=cache, data_hash="dh", task_hash_value="th",
    )
    assert second.from_cache is True
    assert client.calls == 1, "命中缓存不得再次调用模型"
    assert second.analysis is not None


def test_force_retry_bypasses_cache(demo, tmp_path) -> None:
    cache = ai_analyzer.AnalysisCache(tmp_path / "cache.json")
    client = FakeClient(
        [valid_payload(demo["creator_id"]), valid_payload(demo["creator_id"])]
    )
    creator = demo["creators"][demo["creator_id"]]
    assessment = demo["assessments"][demo["creator_id"]]
    for _ in range(2):
        ai_analyzer.analyze_one(
            creator, assessment, DEMO_TASK, client, template="t {{payload}}",
            cache=cache, data_hash="dh", task_hash_value="th", force=True,
        )
    assert client.calls == 2


def test_cache_key_changes_with_data_and_model(demo) -> None:
    base = ai_analyzer.AnalysisCache.make_key("c1", "d1", "t1", "v1", "m1")
    assert base != ai_analyzer.AnalysisCache.make_key("c1", "d2", "t1", "v1", "m1")
    assert base != ai_analyzer.AnalysisCache.make_key("c1", "d1", "t1", "v1", "m2")
    assert base == ai_analyzer.AnalysisCache.make_key("c1", "d1", "t1", "v1", "m1")


def test_estimate_requests_counts_cache_hits(demo, tmp_path) -> None:
    cache = ai_analyzer.AnalysisCache(tmp_path / "cache.json")
    pairs = demo["pairs"]
    estimate = ai_analyzer.estimate_requests(pairs, "dh", "th", "fake-model-v1", cache)
    assert estimate["candidates"] == 3 and estimate["to_call"] == 3 and estimate["cached"] == 0
    client = FakeClient([valid_payload(c.creator_id) for c, _ in pairs])
    records = ai_analyzer.analyze_candidates(
        pairs, DEMO_TASK, client, template="t {{payload}}",
        cache=cache, data_hash="dh", task_hash_value="th",
    )
    assert [r.status for r in records] == ["ok", "ok", "ok"]
    again = ai_analyzer.estimate_requests(pairs, "dh", "th", "fake-model-v1", cache)
    assert again["cached"] == 3 and again["to_call"] == 0


def test_results_wrapper_and_id_mismatch(demo) -> None:
    wrapped = {"results": [valid_payload(demo["creator_id"])]}
    analysis = ai_analyzer.parse_analysis(wrapped, demo["creator_id"])
    assert analysis.creator_id == demo["creator_id"]
    with pytest.raises(AIError) as excinfo:
        ai_analyzer.parse_analysis({"results": [valid_payload("someone_else")]}, demo["creator_id"])
    assert excinfo.value.kind == "format"


def test_scrub_hides_keys() -> None:
    assert "sk-" not in scrub("Authorization: Bearer sk-abcdef123456")
    assert "已隐藏" in scrub("api_key=sk-abcdef123456")


def test_classify_exception_maps_status_codes() -> None:
    class Boom(Exception):
        def __init__(self, status_code):
            self.status_code = status_code
            super().__init__(f"status {status_code}")

    assert classify_exception(Boom(401)).kind == "auth"
    assert classify_exception(Boom(429)).kind == "rate_limit"
    assert classify_exception(Boom(503)).kind == "server"
    assert classify_exception(Boom(400)).kind == "request"
    assert classify_exception(TimeoutError("timed out")).kind == "timeout"
    assert classify_exception(Boom(401)).retryable is False
    assert classify_exception(Boom(429)).retryable is True


def test_deepseek_client_retries_then_succeeds(monkeypatch) -> None:
    client = DeepSeekClient(api_key="sk-fake-for-test", max_retries=2)
    calls = {"n": 0}

    def fake_request(system_prompt: str, user_prompt: str) -> ChatResult:
        calls["n"] += 1
        if calls["n"] < 3:
            raise AIError("timeout", "超时")
        return ChatResult(text='{"ok": true}', model="deepseek-chat", attempts=1)

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr("src.ai_client.time.sleep", lambda *_: None)
    result = client.complete_json("s", "u")
    assert calls["n"] == 3 and result.attempts == 3 and result.data == {"ok": True}


def test_deepseek_client_gives_up_after_limit(monkeypatch) -> None:
    client = DeepSeekClient(api_key="sk-fake-for-test", max_retries=2)
    calls = {"n": 0}

    def always_timeout(system_prompt: str, user_prompt: str) -> ChatResult:
        calls["n"] += 1
        raise AIError("timeout", "超时")

    monkeypatch.setattr(client, "_request", always_timeout)
    monkeypatch.setattr("src.ai_client.time.sleep", lambda *_: None)
    with pytest.raises(AIError) as excinfo:
        client.complete_json("s", "u")
    assert excinfo.value.kind == "timeout"
    assert calls["n"] == 3, "最多尝试 3 次（1 次原始 + 2 次重试）"


def test_deepseek_client_does_not_retry_auth_error(monkeypatch) -> None:
    client = DeepSeekClient(api_key="sk-fake-for-test", max_retries=2)
    calls = {"n": 0}

    def unauthorized(system_prompt: str, user_prompt: str) -> ChatResult:
        calls["n"] += 1
        raise AIError("auth", "鉴权失败")

    monkeypatch.setattr(client, "_request", unauthorized)
    with pytest.raises(AIError):
        client.complete_json("s", "u")
    assert calls["n"] == 1, "鉴权失败重试没有意义，不应反复调用"


def test_client_is_none_without_api_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    assert DeepSeekClient.from_env(tmp_path / "no-such.env") is None


def test_parse_json_object_handles_code_fence() -> None:
    from src.ai_client import parse_json_object

    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(AIError):
        parse_json_object("这不是 JSON")
