"""界面行为测试：用 Streamlit 自带的 AppTest 真正跑一遍界面逻辑。

覆盖两个曾经出问题的点：
1. ①任务配置保存后必须看得到成功提示（提示要出现在页面顶部，而不是按钮下方）。
2. ④人工审核必须有显眼的保存按钮（原来是 24 行表单下面的按钮，用户找不到）。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

import json

from src import ai_analyzer, rule_engine
from src import snapshot
from src.ai_client import ChatResult
from src.data_loader import DEMO_TASK, load_demo_dataset
from src.models import RuleConfig

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def new_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def button_labels(at: AppTest) -> list[str]:
    return [b.label for b in at.button]


def click_button(at: AppTest, label: str) -> AppTest:
    matches = [b for b in at.button if b.label == label]
    assert matches, f"页面上找不到按钮：{label}（当前按钮：{button_labels(at)}）"
    return matches[0].click().run()


def seed_result_state(at: AppTest) -> AppTest:
    """预置「已经跑完规则初筛」的状态，用于测试 ④ / ⑤ 两个页面。"""
    dataset = load_demo_dataset()
    at.session_state["dataset"] = dataset
    at.session_state["task"] = DEMO_TASK
    at.session_state["assessments"] = rule_engine.evaluate_dataset(
        dataset.creators, DEMO_TASK, RuleConfig()
    )
    return at


def test_app_starts_without_exception() -> None:
    at = new_app().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("保存任务配置" == label for label in button_labels(at))


def test_task_step_shows_success_message_after_save() -> None:
    at = new_app().run()
    at = click_button(at, "保存任务配置")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["task"].product_name == DEMO_TASK.product_name
    messages = [s.value for s in at.success]
    assert any("任务配置已保存" in m for m in messages), messages
    # 提示必须出现在表单之前（页面上方），用户无需滚动就能看到
    body = " ".join(str(m) for m in at.info) + " ".join(messages)
    assert "任务配置已保存" in body


def test_task_step_blocks_empty_required_fields() -> None:
    at = new_app().run()
    at.text_input[0].set_value("")
    at = click_button(at, "保存任务配置")
    assert not at.exception
    errors = [e.value for e in at.error]
    assert any("产品名称为必填" in e for e in errors), errors
    assert at.session_state["task"] is None


def test_review_step_has_visible_save_button() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("⑤ 人工审核").run()
    assert not at.exception, [e.value for e in at.exception]
    assert "保存人工审核结果" in button_labels(at), button_labels(at)


def test_review_step_saves_decisions_and_confirms() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("⑤ 人工审核").run()
    # 先给第一位达人一个明确结论（状态选择框是页面上第 2 个 selectbox）
    at.selectbox[1].set_value("建议联系").run()
    at = click_button(at, "应用到这位达人")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["decisions"], "点应用后必须记录人工结论"
    at = click_button(at, "保存人工审核结果")
    assert not at.exception, [e.value for e in at.exception]
    decisions = at.session_state["decisions"]
    assert all(d.creator_id for d in decisions.values())
    assert any("已保存" in s.value for s in at.success), [s.value for s in at.success]


def test_review_step_apply_and_next_moves_to_another_creator() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("⑤ 人工审核").run()
    first_label = at.session_state["review_selected"]
    first_id = first_label.split(" · ")[-1]
    at = click_button(at, "应用并跳到下一位")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["review_selected"] != first_label, "「下一位」必须切换到另一位达人"
    assert first_id in at.session_state["decisions"], at.session_state["decisions"]


def test_review_step_has_no_inline_editable_grid() -> None:
    """回归测试：4 号页面不能再出现滚动时错位的内联可编辑表格。"""
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("⑤ 人工审核").run()
    assert not at.exception, [e.value for e in at.exception]
    assert "逐条审核" in " ".join(m.value for m in at.markdown), "应使用逐条审核面板"
    assert "应用到这位达人" in button_labels(at), button_labels(at)


def test_review_page_excludes_excluded_creators() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("⑤ 人工审核").run()
    assessments = at.session_state["assessments"]
    excluded = {a.creator_id for a in assessments if a.group == "excluded"}
    assert excluded, "演示数据里应当存在被排除的候选"
    captions = " ".join(c.value for c in at.caption)
    assert f"已排除的 {len(excluded)} 条" in captions, captions


def test_export_page_ready_after_rules_run() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("⑥ 导出").run()
    assert not at.exception, [e.value for e in at.exception]


class EchoFakeClient:
    """测试用假客户端：从 prompt 里读出真实数据，返回引用真实原文的最小合法结果。"""

    model = "fake-echo"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> ChatResult:
        self.calls += 1
        start = user_prompt.rfind('{\n  "task"')
        payload, _ = json.JSONDecoder().raw_decode(user_prompt[start:])
        creator = payload["creators"][0]
        if creator.get("sample_content_text"):
            field, quote = "sample_content_text", creator["sample_content_text"][:12]
        else:
            field, quote = "creator_name", str(creator.get("creator_name") or "未知")
        data = {
            "creator_id": creator["creator_id"],
            "analysis_status": "ok",
            "content_relevance": {"verdict": "strong", "reason": "内容贴合", "evidence": [{"field": field, "quote": quote}]},
            "goal_fit": {"verdict": "moderate", "reason": "适合种草", "evidence": []},
            "audience_fit": {"verdict": "unknown", "reason": "缺受众数据", "evidence": []},
            "recommendation_reasons": ["内容主题贴合"],
            "mismatches": [],
            "risks": [],
            "missing_info": [],
            "human_checks": ["确认报价"],
            "data_conflicts": [],
            "confidence": "medium",
        }
        return ChatResult(
            text=json.dumps(data, ensure_ascii=False), data=data, model=self.model, attempts=1
        )


def test_ai_page_without_api_key_offers_no_fake_results(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("④ AI 语义分析").run()
    assert not at.exception, [e.value for e in at.exception]
    warnings = " ".join(w.value for w in at.warning)
    assert "未配置 DEEPSEEK_API_KEY" in warnings, warnings
    assert "运行 AI 分析" not in button_labels(at), "没有 Key 时不应给出可运行的假入口"
    assert at.session_state["ai_records"] == {}


def test_ai_page_runs_with_injected_test_client(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_analyzer, "RUNTIME_DIR", tmp_path)
    at = seed_result_state(new_app())
    client = EchoFakeClient()
    at.session_state["ai_client_override"] = client
    at.run()
    at.sidebar.radio[0].set_value("④ AI 语义分析").run()
    assert not at.exception, [e.value for e in at.exception]
    at.number_input[0].set_value(2).run()
    at.checkbox[1].set_value(True).run()
    at = click_button(at, "运行 AI 分析")
    assert not at.exception, [e.value for e in at.exception]
    records = at.session_state["ai_records"]
    assert len(records) == 2, records
    assert client.calls == 2
    assert all(r.status == "ok" for r in records.values())
    assert all(not r.evidence_problems for r in records.values())
    assert any("AI 分析完成" in s.value for s in at.success)


def test_sidebar_shows_version_so_we_can_tell_which_code_is_running() -> None:
    at = new_app().run()
    captions = " ".join(c.value for c in at.sidebar.caption)
    assert snapshot.APP_VERSION in captions, captions


def test_rules_step_run_button_is_first_and_writes_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(snapshot, "RUNS_DIR", tmp_path / "runs")
    at = new_app()
    at.session_state["dataset"] = load_demo_dataset()
    at.session_state["task"] = DEMO_TASK
    at.run()
    at.sidebar.radio[0].set_value("③ 规则初筛").run()
    assert not at.exception, [e.value for e in at.exception]
    assert button_labels(at)[0] == "运行规则初筛", button_labels(at)
    at = click_button(at, "运行规则初筛")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["assessments"]
    assert (tmp_path / "runs").exists(), "运行规则初筛必须写下快照"
    assert any("规则初筛完成" in s.value for s in at.success), [s.value for s in at.success]
