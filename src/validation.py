"""校验结果的可读化：字段覆盖率、预览、修复建议。"""

from __future__ import annotations

from .models import CreatorRecord, ValidationReport

KEY_FIELDS: tuple[str, ...] = (
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
)


def field_coverage(creators: list[CreatorRecord]) -> list[dict[str, object]]:
    """每个字段有多少条记录是非空的，用于判断数据是否够用。"""
    total = len(creators)
    rows: list[dict[str, object]] = []
    for field in KEY_FIELDS:
        present = sum(1 for c in creators if getattr(c, field) not in (None, [], ""))
        rows.append(
            {
                "field": field,
                "present": present,
                "total": total,
                "coverage": (present / total) if total else 0.0,
            }
        )
    return rows


def preview_rows(creators: list[CreatorRecord], limit: int = 10) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for creator in creators[:limit]:
        rows.append(
            {
                "creator_id": creator.creator_id,
                "creator_name": creator.creator_name or "",
                "platform": creator.platform or "",
                "market": creator.market or "",
                "language": creator.language or "",
                "follower_count": creator.follower_count if creator.follower_count is not None else "—",
                "engagement_rate": (
                    f"{creator.engagement_rate:.2%}" if creator.engagement_rate is not None else "—"
                ),
                "missing_fields": len(creator.missing_fields),
            }
        )
    return rows


def summarize(report: ValidationReport) -> dict[str, object]:
    errors = report.by_level("error")
    warnings = report.by_level("warning")
    infos = report.by_level("info")
    suggestions = sorted({i.suggestion for i in errors + warnings if i.suggestion})
    return {
        "row_count": report.row_count,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "info_count": len(infos),
        "duplicate_ids": report.duplicate_ids,
        "unknown_fields": report.unknown_fields,
        "encoding_used": report.encoding_used,
        "can_continue": not report.droppable,
        "suggestions": suggestions,
    }
