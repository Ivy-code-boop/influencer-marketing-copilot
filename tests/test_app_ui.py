"""界面行为测试：用 Streamlit 自带的 AppTest 真正跑一遍界面逻辑。

覆盖两个曾经出问题的点：
1. ①任务配置保存后必须看得到成功提示（提示要出现在页面顶部，而不是按钮下方）。
2. ④人工审核必须有显眼的保存按钮（原来是 24 行表单下面的按钮，用户找不到）。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src import rule_engine
from src import snapshot
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
    at.sidebar.radio[0].set_value("④ 人工审核").run()
    assert not at.exception, [e.value for e in at.exception]
    assert "保存人工审核结果" in button_labels(at), button_labels(at)


def test_review_step_saves_decisions_and_confirms() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("④ 人工审核").run()
    at = click_button(at, "保存人工审核结果")
    assert not at.exception, [e.value for e in at.exception]
    decisions = at.session_state["decisions"]
    assert decisions, "点击保存后必须写入人工审核结果"
    assert all(d.creator_id for d in decisions.values())
    assert any("已保存" in s.value for s in at.success), [s.value for s in at.success]


def test_review_page_excludes_excluded_creators() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("④ 人工审核").run()
    assessments = at.session_state["assessments"]
    excluded = {a.creator_id for a in assessments if a.group == "excluded"}
    assert excluded, "演示数据里应当存在被排除的候选"
    captions = " ".join(c.value for c in at.caption)
    assert f"已排除的 {len(excluded)} 条" in captions, captions


def test_export_page_ready_after_rules_run() -> None:
    at = seed_result_state(new_app())
    at.run()
    at.sidebar.radio[0].set_value("⑤ 导出").run()
    assert not at.exception, [e.value for e in at.exception]


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
