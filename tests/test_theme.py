"""视觉基础层测试：主题配置、CSS 集中管理、注入是否生效。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui.styles import GLOBAL_CSS

ROOT = Path(__file__).resolve().parent.parent


def test_theme_config_has_required_tokens() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    theme = config["theme"]
    assert theme["primaryColor"].lower() == "#4f46e5"
    assert theme["backgroundColor"].lower() == "#f6f7f9"
    assert theme["secondaryBackgroundColor"].lower() == "#ffffff"
    assert theme["textColor"].lower() == "#0f172a"
    assert theme["font"] == "sans serif"


def test_css_covers_requested_rules() -> None:
    css = GLOBAL_CSS
    # 布局
    assert "max-width: 1160px" in css
    assert "margin: 0 auto" in css
    # 圆角
    assert "--imc-radius-card: 12px" in css
    assert "--imc-radius-control: 8px" in css
    # 阴影与动效
    assert "box-shadow" in css
    assert "transition:" in css
    assert "translateY(-1px)" in css
    # 输入框 focus
    assert ":focus-within" in css
    # 隐藏菜单与页脚
    assert "#MainMenu" in css
    assert "footer" in css
    # 字体栈
    assert "Inter" in css and "-apple-system" in css


def test_css_is_centralized_out_of_app() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "<style" not in app_source, "CSS 不能散落在 app.py 里"
    assert "from ui.styles import GLOBAL_CSS" in app_source
    assert "st.markdown(GLOBAL_CSS, unsafe_allow_html=True)" in app_source


def test_app_injects_global_css() -> None:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    rendered = " ".join(m.value for m in at.markdown)
    assert "imc-global-css" in rendered
