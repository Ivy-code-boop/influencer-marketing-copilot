"""数据加载与解析的测试：编码、格式、缺失、重复、错误行。"""

from __future__ import annotations

from src.data_loader import (
    csv_to_text,
    detect_encoding,
    load_dataset_from_csv_text,
    load_demo_dataset,
    parse_int,
    parse_number,
    parse_rate,
)

HEADER = [
    "creator_id",
    "creator_name",
    "platform",
    "market",
    "language",
    "follower_count",
    "average_views",
    "likes",
    "comments",
    "engagement_rate",
    "content_categories",
    "recent_content_summary",
    "sample_content_text",
    "audience_description",
    "collaboration_history",
    "data_source",
    "data_updated_at",
    "data_quality_notes",
]


def make_row(**overrides: str) -> dict[str, str]:
    row = {col: "" for col in HEADER}
    row.update(
        {
            "creator_id": "c1",
            "creator_name": "测试达人",
            "platform": "TikTok",
            "market": "德国",
            "language": "德语",
            "content_categories": "咖啡",
            "data_source": "单元测试",
        }
    )
    row.update(overrides)
    return row


def load(text: str):
    return load_dataset_from_csv_text(
        text, source_name="inline.csv", origin="upload", encoding_used="utf-8", encoding_warnings=[]
    )


def test_parse_number_handles_common_formats() -> None:
    assert parse_number("1,234")[0] == 1234
    assert parse_number("12.5K")[0] == 12500
    assert parse_number("1.2M")[0] == 1200000
    assert parse_number("4.2%")[0] == 0.042
    assert parse_number(" 892 ")[0] == 892
    assert parse_number("")[0] is None
    value, err = parse_number("不是数字")
    assert value is None and err


def test_parse_int_and_rate() -> None:
    assert parse_int("1.2K")[0] == 1200
    assert parse_rate("4.2%")[0] == 0.042
    assert parse_rate("0.018")[0] == 0.018
    assert parse_rate("1.9%")[0] == 0.019


def test_gbk_file_is_detected_and_read() -> None:
    text = csv_to_text([make_row()], HEADER)
    encoding, warnings = detect_encoding(text.encode("gb18030"))
    assert encoding in {"gb18030", "gbk"}
    assert warnings
    dataset = load_dataset_from_csv_text(
        text.encode("gb18030").decode("gb18030"),
        source_name="gbk.csv",
        origin="upload",
        encoding_used=encoding,
        encoding_warnings=warnings,
    )
    assert dataset.creators[0].creator_name == "测试达人"


def test_duplicate_ids_are_reported() -> None:
    rows = [make_row(), make_row(creator_name="重复达人")]
    dataset = load(csv_to_text(rows, HEADER))
    assert dataset.report.duplicate_ids == ["c1"]
    assert dataset.report.has_blocking_error
    assert len(dataset.creators) == 1


def test_missing_creator_id_blocks_run() -> None:
    rows = [make_row(creator_id="")]
    dataset = load(csv_to_text(rows, HEADER))
    assert dataset.report.droppable
    assert any(i.field == "creator_id" and i.level == "error" for i in dataset.report.issues)
    assert dataset.creators == []


def test_empty_file_is_reported() -> None:
    dataset = load("creator_id\n")
    assert dataset.report.droppable
    assert dataset.creators == []


def test_missing_values_are_recorded_not_dropped() -> None:
    rows = [make_row(follower_count="", engagement_rate="")]
    dataset = load(csv_to_text(rows, HEADER))
    creator = dataset.creators[0]
    assert creator.follower_count is None
    assert creator.engagement_rate is None
    assert "follower_count" in creator.missing_fields
    assert "engagement_rate" in creator.missing_fields


def test_bad_number_produces_warning_and_keeps_row() -> None:
    rows = [make_row(follower_count="十万")]
    dataset = load(csv_to_text(rows, HEADER))
    assert len(dataset.creators) == 1
    assert dataset.creators[0].follower_count is None
    assert any("无法解析" in i.message for i in dataset.report.issues)


def test_unknown_columns_are_reported() -> None:
    rows = [make_row(extra_col="x")]
    dataset = load(csv_to_text(rows, HEADER + ["extra_col"]))
    assert "extra_col" in dataset.report.unknown_fields


def test_demo_dataset_loads_thirty_rows_without_errors() -> None:
    dataset = load_demo_dataset()
    assert len(dataset.creators) >= 30
    assert not dataset.report.has_blocking_error
    assert all("[模拟数据]" in (c.data_quality_notes or "") for c in dataset.creators)


def test_demo_dataset_covers_missing_and_format_cases() -> None:
    dataset = load_demo_dataset()
    missing_any = [c for c in dataset.creators if c.missing_fields]
    assert missing_any, "演示数据必须包含字段缺失的样例"
    assert any(c.follower_count and c.follower_count > 100_000 for c in dataset.creators)
