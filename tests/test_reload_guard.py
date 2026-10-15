"""回归测试：不能再出现「改了 src/ 下的模块后页面报 ImportError」的问题。

背景：Streamlit 只重跑主脚本、不重新导入被 import 的模块，导致进程里残留旧模块。
app.py 顶部会在每次重跑前清掉 src 包的模块缓存（测试环境跳过）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_SOURCE = Path(__file__).resolve().parent.parent / "app.py"


def test_app_purges_stale_src_modules_before_importing() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")
    purge_index = source.find("del sys.modules[_stale_name]")
    import_index = source.find("from src import")
    assert purge_index != -1, "app.py 必须清理 src 模块缓存，否则改模块后页面会报 ImportError"
    assert purge_index < import_index, "清理动作必须发生在导入 src 之前"
    assert "PYTEST_CURRENT_TEST" in source, "测试环境必须跳过清理，否则 monkeypatch 会失效"


def test_purging_module_cache_yields_fresh_module_objects() -> None:
    """清理缓存后重新导入会拿到新的模块对象，也就是新的代码。"""
    import src.models as before

    stale = [name for name in list(sys.modules) if name == "src" or name.startswith("src.")]
    assert stale, "测试进程里应当已经加载过 src 包"
    for name in stale:
        del sys.modules[name]
    import src.models as after

    assert before is not after


def test_guard_is_skipped_during_pytest() -> None:
    assert os.environ.get("PYTEST_CURRENT_TEST"), "pytest 运行时应带上该环境变量"
    assert "src" in sys.modules, "测试期间不应清理 src 模块缓存"
