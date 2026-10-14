"""数据加载与数值解析。

支持两种来源：项目内置演示数据、用户上传的 CSV。
解析原则：不静默丢弃任何行或字段；无法解析的内容必须出现在校验报告里。
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd

from .models import (
    CreatorRecord,
    Dataset,
    MarketingTask,
    ValidationIssue,
    ValidationReport,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_CSV = PROJECT_ROOT / "data" / "demo" / "demo_creators.csv"

REQUIRED_COLUMN = "creator_id"

# CSV 列名 -> 模型字段名（英文小写下划线，若列名已是字段名则原样保留）
CANONICAL_FIELDS: tuple[str, ...] = (
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
)

# 常见别名，便于用户上传自己的 CSV
COLUMN_ALIASES: dict[str, str] = {
    "id": "creator_id",
    "达人id": "creator_id",
    "达人编号": "creator_id",
    "name": "creator_name",
    "达人名称": "creator_name",
    "达人": "creator_name",
    "平台": "platform",
    "市场": "market",
    "国家": "market",
    "地区": "market",
    "语言": "language",
    "粉丝数": "follower_count",
    "粉丝量": "follower_count",
    "followers": "follower_count",
    "平均播放量": "average_views",
    "播放量": "average_views",
    "avg_views": "average_views",
    "点赞": "likes",
    "点赞数": "likes",
    "评论": "comments",
    "评论数": "comments",
    "互动率": "engagement_rate",
    "内容领域": "content_categories",
    "类目": "content_categories",
    "近期内容": "recent_content_summary",
    "内容摘要": "recent_content_summary",
    "代表内容": "sample_content_text",
    "内容文本": "sample_content_text",
    "受众": "audience_description",
    "受众描述": "audience_description",
    "合作经历": "collaboration_history",
    "数据来源": "data_source",
    "数据更新时间": "data_updated_at",
    "数据质量说明": "data_quality_notes",
}

TEXT_FIELDS: tuple[str, ...] = (
    "creator_name",
    "platform",
    "market",
    "language",
    "recent_content_summary",
    "sample_content_text",
    "audience_description",
    "collaboration_history",
    "data_source",
    "data_updated_at",
    "data_quality_notes",
)
NUMERIC_FIELDS: tuple[str, ...] = (
    "follower_count",
    "average_views",
    "likes",
    "comments",
)
RATE_FIELDS: tuple[str, ...] = ("engagement_rate",)

ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1")

_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "万": 10_000, "亿": 100_000_000}


def _clean(raw: str) -> str:
    return raw.strip().strip('"').strip()


def is_missing(raw: object) -> bool:
    """空值判定：None、空串、以及常见的占位符都算缺失。"""
    if raw is None:
        return True
    text = _clean(str(raw))
    if text == "":
        return True
    return text.lower() in {"na", "n/a", "none", "null", "-", "--", "nan", "未知", "无"}


def parse_number(raw: object) -> tuple[float | None, str | None]:
    """把 '1,234' / '12.5K' / '1.2M' / '4.2%' / ' 892 ' 解析成数字。

    返回 (数值, 错误信息)。无法解析时返回 (None, 原因)，由调用方写入校验报告。
    """
    if is_missing(raw):
        return None, None
    text = _clean(str(raw))
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    mult = 1.0
    suffix = text[-1:].lower()
    if suffix and not suffix.isdigit() and suffix not in {".", ",", "+", "-"}:
        if suffix in _MULTIPLIERS:
            mult = float(_MULTIPLIERS[suffix])
            text = text[:-1].strip()
    text = text.replace(",", "").replace(" ", "").replace("+", "")
    try:
        value = float(text) * mult
    except ValueError:
        return None, f"无法解析为数字：{raw!r}"
    if percent and mult == 1.0:
        value = value / 100.0
    return value, None


def parse_int(raw: object) -> tuple[int | None, str | None]:
    value, err = parse_number(raw)
    if err:
        return None, err
    if value is None:
        return None, None
    return int(round(value)), None


def parse_rate(raw: object) -> tuple[float | None, str | None]:
    """互动率统一存成小数：'4.2%' -> 0.042；已经是小数的原样保留。"""
    if is_missing(raw):
        return None, None
    text = _clean(str(raw))
    value, err = parse_number(raw)
    if err:
        return None, err
    assert value is not None
    if text.endswith("%"):
        return value, None
    return (value / 100.0 if value > 1 else value), None


def parse_categories(raw: object) -> list[str]:
    if is_missing(raw):
        return []
    text = _clean(str(raw))
    parts = re.split(r"[;,、|/]+|\s{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def detect_encoding(raw_bytes: bytes) -> tuple[str, list[str]]:
    """按候选编码依次尝试解码，返回 (编码, 警告列表)。"""
    warnings: list[str] = []
    for enc in ENCODINGS:
        try:
            raw_bytes.decode(enc)
        except UnicodeDecodeError as exc:
            warnings.append(f"{enc} 解码失败：{exc}")
            continue
        if enc != ENCODINGS[0]:
            warnings.append(f"文件不是 UTF-8，已按 {enc} 读取。建议另存为 UTF-8 以免跨平台乱码。")
        return enc, warnings
    return ENCODINGS[-1], warnings + ["全部候选编码均失败，已按 latin-1 强行读取，请检查文件。"]


def normalize_columns(columns: list[str]) -> tuple[dict[str, str], list[str]]:
    """返回 (列名映射, 未识别列)。重名的规范字段保留第一个出现的列。"""
    mapping: dict[str, str] = {}
    unknown: list[str] = []
    for col in columns:
        key = col.strip()
        lower = key.lower().replace(" ", "_")
        if lower in CANONICAL_FIELDS:
            canonical = lower
        elif key in COLUMN_ALIASES:
            canonical = COLUMN_ALIASES[key]
        elif lower in COLUMN_ALIASES:
            canonical = COLUMN_ALIASES[lower]
        else:
            unknown.append(key)
            continue
        mapping.setdefault(key, canonical)
    return mapping, unknown


def read_csv_text(path: Path) -> tuple[str, str, list[str]]:
    raw = path.read_bytes()
    encoding, warnings = detect_encoding(raw)
    return raw.decode(encoding, errors="replace"), encoding, warnings


def load_dataset_from_csv_text(
    text: str, source_name: str, origin: str, encoding_used: str, encoding_warnings: list[str]
) -> Dataset:
    report = ValidationReport(encoding_used=encoding_used)
    report.issues.extend(
        ValidationIssue(level="warning", message=w, suggestion="")
        for w in encoding_warnings
    )

    frame = pd.read_csv(
        io.StringIO(text),
        dtype=str,
        keep_default_na=False,
        skip_blank_lines=True,
    )
    if frame.empty:
        report.issues.append(
            ValidationIssue(level="error", message="文件没有数据行。", suggestion="确认 CSV 至少包含一行达人数据。")
        )
        report.droppable = True
        return Dataset(source_name=source_name, origin=origin, creators=[], report=report)

    mapping, unknown = normalize_columns(list(frame.columns))
    report.mapped_fields = {k: v for k, v in mapping.items()}
    report.unknown_fields = unknown
    if unknown:
        report.issues.append(
            ValidationIssue(
                level="warning",
                field=",".join(unknown),
                message=f"以下列未被识别，已保留在原始文件中但不参与分析：{', '.join(unknown)}",
                suggestion="如这些列有意义，请在 docs 或 README 中补充字段说明后再映射。",
            )
        )

    canonical_to_column: dict[str, str] = {}
    for col, canonical in mapping.items():
        canonical_to_column.setdefault(canonical, col)

    if REQUIRED_COLUMN not in canonical_to_column:
        report.issues.append(
            ValidationIssue(
                level="error",
                field=REQUIRED_COLUMN,
                message="缺少必要字段 creator_id（达人唯一标识）。",
                suggestion="在 CSV 中增加 creator_id 列，或改名为 id / 达人id 后重新上传。",
            )
        )
        report.droppable = True
        return Dataset(source_name=source_name, origin=origin, creators=[], report=report)

    seen: set[str] = set()
    creators: list[CreatorRecord] = []
    for idx, row in frame.iterrows():
        row_no = int(idx) + 2  # +2：CSV 第 1 行是表头

        def value(canonical: str) -> str | None:
            col = canonical_to_column.get(canonical)
            if col is None:
                return None
            raw = row.get(col)
            if is_missing(raw):
                return None
            return str(raw).strip()

        creator_id = value("creator_id")
        if not creator_id:
            report.issues.append(
                ValidationIssue(
                    level="error",
                    row=row_no,
                    field="creator_id",
                    message=f"第 {row_no} 行的 creator_id 为空。",
                    suggestion="补齐唯一 ID，或从文件中删除该行。",
                )
            )
            continue
        if creator_id in seen:
            report.duplicate_ids.append(creator_id)
            report.issues.append(
                ValidationIssue(
                    level="error",
                    row=row_no,
                    field="creator_id",
                    message=f"creator_id 重复：{creator_id}。",
                    suggestion="合并重复记录或改用唯一 ID；重复行已被跳过以免污染统计。",
                )
            )
            continue
        seen.add(creator_id)

        warnings: list[str] = []
        data: dict[str, object] = {"creator_id": creator_id}
        for field in TEXT_FIELDS:
            data[field] = value(field)
        data["content_categories"] = parse_categories(value("content_categories"))

        for field in NUMERIC_FIELDS:
            parsed, err = parse_int(value(field))
            data[field] = parsed
            if err:
                warnings.append(f"{field}: {err}")

        rate, rate_err = parse_rate(value("engagement_rate"))
        data["engagement_rate"] = rate
        if rate_err:
            warnings.append(f"engagement_rate: {rate_err}")
        if rate is not None:
            data["engagement_rate_source"] = "来自数据文件（口径由数据提供方说明）"

        for warn in warnings:
            report.issues.append(
                ValidationIssue(
                    level="warning",
                    row=row_no,
                    field=warn.split(":")[0],
                    message=f"第 {row_no} 行 {warn}",
                    suggestion="修正该单元格格式（数字可用 1,234 / 12.5K / 1.2M；百分比可写 4.2%），或留空表示缺失。",
                )
            )

        missing = [
            f
            for f in CANONICAL_FIELDS
            if f not in {"creator_id"} and data.get(f) in (None, [], "")
        ]
        data["missing_fields"] = missing
        data["parse_warnings"] = warnings
        creators.append(CreatorRecord(**data))

    report.row_count = len(creators)
    if not creators:
        report.droppable = True
        report.issues.append(
            ValidationIssue(level="error", message="没有解析出任何有效达人记录。", suggestion="检查 creator_id 列与文件内容。")
        )
    report.droppable = report.droppable or report.has_blocking_error
    return Dataset(source_name=source_name, origin=origin, creators=creators, report=report)


def load_demo_dataset(path: Path | None = None) -> Dataset:
    csv_path = path or DEMO_CSV
    text, encoding, warnings = read_csv_text(csv_path)
    dataset = load_dataset_from_csv_text(
        text,
        source_name=csv_path.name,
        origin="demo",
        encoding_used=encoding,
        encoding_warnings=warnings,
    )
    for creator in dataset.creators:
        creator.data_quality_notes = (
            (creator.data_quality_notes + " ") if creator.data_quality_notes else ""
        ) + "[模拟数据] 本记录为项目内置演示数据，不代表任何真实达人。"
    return dataset


def load_upload_dataset(file_bytes: bytes, filename: str) -> Dataset:
    encoding, warnings = detect_encoding(file_bytes)
    text = file_bytes.decode(encoding, errors="replace")
    return load_dataset_from_csv_text(
        text,
        source_name=filename,
        origin="upload",
        encoding_used=encoding,
        encoding_warnings=warnings,
    )


DEMO_TASK = MarketingTask(
    product_name="便携电动奶泡杯",
    product_pitch="USB 充电、30 秒打出冷热奶泡，主打租房小厨房与通勤咖啡场景，价格中等。",
    target_market="德国",
    target_platform="TikTok",
    marketing_goal="content_seeding",
    target_audience="20-35 岁、喜欢在家做咖啡的城市上班族",
    collaboration_type="产品置换 + 少量付费",
    budget_range="500-2000 EUR",
    min_follower_count=10_000,
    required_language="德语",
)


def csv_to_text(rows: list[dict[str, str]], columns: list[str]) -> str:
    """测试与演示用：把字典列表转成 CSV 文本。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
