"""缓存必须只做优化：写不进去也不能影响分析结果。"""

from __future__ import annotations

from src import ai_analyzer


def test_cache_write_failure_does_not_raise(tmp_path) -> None:
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")
    cache = ai_analyzer.AnalysisCache(blocker / "ai_cache.json")
    cache.set("k", {"creator_id": "c1"})  # 不应抛异常
    assert cache.write_error, "写失败必须被记录下来，而不是静默"
    assert cache.get("k") == {"creator_id": "c1"}, "内存里仍然可用"


def test_cache_roundtrip(tmp_path) -> None:
    path = tmp_path / "ai_cache.json"
    first = ai_analyzer.AnalysisCache(path)
    first.set("k", {"creator_id": "c1", "status": "ok"})
    assert first.write_error is None
    assert len(first) == 1
    second = ai_analyzer.AnalysisCache(path)
    assert second.get("k") == {"creator_id": "c1", "status": "ok"}


def test_corrupted_cache_file_is_ignored(tmp_path) -> None:
    path = tmp_path / "ai_cache.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    cache = ai_analyzer.AnalysisCache(path)
    assert len(cache) == 0
    cache.set("k", {"a": 1})
    assert ai_analyzer.AnalysisCache(path).get("k") == {"a": 1}
