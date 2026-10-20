"""全局骨架测试：产品栏、运行状态条、侧边栏进度、HTML 转义。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui import components

ROOT = Path(__file__).resolve().parent.parent


def test_product_bar_contains_brand_and_chips() -> None:
    markup = components.product_bar(
        "测试产品", "一句话定位", [("规则 v1", "neutral"), ("Prompt v1", "ai")]
    )
    assert "imc-product-bar" in markup
    assert "imc-logo" in markup
    assert "测试产品" in markup and "一句话定位" in markup
    assert "imc-chip-ai" in markup


def test_product_bar_escapes_user_content() -> None:
    markup = components.product_bar("<script>alert(1)</script>", "x", [])
    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup


def test_run_status_bar_marks_stale_and_shows_tooltip() -> None:
    markup = components.run_status_bar(
        [("数据 30 条", "success", "abc123")],
        "已过期",
        "warning",
        ["达人数据已变化"],
    )
    assert "imc-runbar-stale" in markup
    assert "已过期" in markup and "达人数据已变化" in markup
    assert 'title="abc123"' in markup


def test_side_progress_percentage() -> None:
    markup = components.side_progress(3, 6, 2)
    assert "流程 3 / 6" in markup
    assert "width:33%" in markup


def test_app_renders_product_bar_and_status_bar() -> None:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    rendered = " ".join(m.value for m in at.markdown)
    assert "AI 海外达人营销助手" in rendered
    assert "运行状态" in rendered
    assert "imc-runbar" in rendered
    assert "Prompt creator_analysis_v1" in rendered
