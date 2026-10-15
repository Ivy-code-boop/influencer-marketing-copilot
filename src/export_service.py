"""导出：结果 CSV + 元数据 JSON 双文件。

导出内容必须包含数据来源、分析时间与版本信息；失败或信息不足的记录保留并标注状态。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from .models import (
    DIMENSION_LABELS,
    REVIEW_STATUS_LABELS,
    VERDICT_LABELS,
    AIAnalysisRecord,
    CreatorAssessment,
    CreatorRecord,
    ReviewDecision,
)

GROUP_LABELS = {"qualified": "通过硬性条件", "needs_review": "需人工核实", "excluded": "已排除"}
AI_STATUS_LABELS = {
    "ok": "已完成",
    "insufficient_info": "信息不足",
    "failed": "失败",
}


def ai_columns(record: AIAnalysisRecord | None) -> dict:
    """把 AI 结论压成几列写进导出文件；没有跑过 AI 就如实写「未运行」。"""
    if record is None:
        return {
            "ai_status": "未运行",
            "ai_provider": "",
            "ai_from_cache": "",
            "ai_content_relevance": "",
            "ai_goal_fit": "",
            "ai_audience_fit": "",
            "ai_confidence": "",
            "ai_summary": "",
            "ai_risks": "",
            "ai_missing_info": "",
            "ai_human_checks": "",
            "ai_evidence_problems": "",
            "ai_numeric_conflicts": "",
        }
    status = AI_STATUS_LABELS.get(record.status, record.status)
    if record.status == "failed":
        status = f"失败（{record.error_label}）"
    analysis = record.analysis
    return {
        "ai_status": status,
        "ai_provider": "演示模式" if record.provider == "demo" else record.provider,
        "ai_from_cache": "是" if record.from_cache else "否",
        "ai_content_relevance": VERDICT_LABELS.get(analysis.content_relevance.verdict, "") if analysis else "",
        "ai_goal_fit": VERDICT_LABELS.get(analysis.goal_fit.verdict, "") if analysis else "",
        "ai_audience_fit": VERDICT_LABELS.get(analysis.audience_fit.verdict, "") if analysis else "",
        "ai_confidence": analysis.confidence if analysis else "",
        "ai_summary": " | ".join(analysis.recommendation_reasons) if analysis else "",
        "ai_risks": " | ".join(f"[{r.type}] {r.detail}" for r in analysis.risks) if analysis else "",
        "ai_missing_info": " | ".join(analysis.missing_info) if analysis else "",
        "ai_human_checks": " | ".join(analysis.human_checks) if analysis else "",
        "ai_evidence_problems": " | ".join(record.evidence_problems),
        "ai_numeric_conflicts": " | ".join(record.numeric_conflicts),
    }


def build_export_rows(
    assessments: list[CreatorAssessment],
    creators: list[CreatorRecord],
    decisions: dict[str, ReviewDecision],
    ai_records: dict[str, AIAnalysisRecord] | None = None,
) -> list[dict]:
    by_id = {c.creator_id: c for c in creators}
    ai_map = ai_records or {}
    rows: list[dict] = []
    for a in assessments:
        creator = by_id.get(a.creator_id)
        decision = decisions.get(a.creator_id, ReviewDecision(creator_id=a.creator_id))
        dim_cols = {
            f"rule_{d.name}": (f"{d.score:.3f}" if d.score is not None else "信息不足")
            for d in a.dimensions
        }
        rows.append(
            {
                "creator_id": a.creator_id,
                "creator_name": creator.creator_name if creator and creator.creator_name else "",
                "platform": creator.platform if creator and creator.platform else "",
                "market": creator.market if creator and creator.market else "",
                "language": creator.language if creator and creator.language else "",
                "follower_count": creator.follower_count if creator and creator.follower_count is not None else "",
                "hard_check_verdict": {"pass": "通过", "fail": "未通过", "unknown": "无法判定"}[
                    a.hard_check.verdict
                ],
                "group": GROUP_LABELS[a.group],
                "rule_score": f"{a.weighted_score:.3f}" if a.weighted_score is not None else "",
                "rule_coverage": f"{a.coverage:.2f}",
                **dim_cols,
                "missing_fields": ",".join(sorted({m for d in a.dimensions for m in d.missing})),
                "rule_reasons": " | ".join(a.reasons),
                **ai_columns(ai_map.get(a.creator_id)),
                "review_status": REVIEW_STATUS_LABELS[decision.status],
                "review_note": decision.note,
            }
        )
    return rows


def rows_to_csv_text(rows: list[dict]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build_export_meta(meta: dict, rows: list[dict]) -> dict:
    return {
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_id": meta.get("run_id"),
        "analyzed_at": meta.get("created_at"),
        "app_version": meta.get("app_version"),
        "data_source": meta.get("data_source"),
        "data_origin": meta.get("data_origin"),
        "data_hash": meta.get("data_hash"),
        "data_row_count": meta.get("data_row_count"),
        "task_hash": meta.get("task_hash"),
        "rule_version": meta.get("rule_version"),
        "rule_config_hash": meta.get("rule_config_hash"),
        "prompt_version": meta.get("prompt_version"),
        "model_requested": meta.get("model_requested"),
        "model_returned": meta.get("model_returned"),
        "exported_row_count": len(rows),
        "failed_or_insufficient_rows": sum(
            1
            for r in rows
            if str(r.get("ai_status", "")).startswith(("失败", "信息不足"))
            or r.get("rule_score") == ""
        ),
        "notes": "导出内容包含全部记录（含无法判定与信息不足），未做删除。",
        "weights_note": "规则权重为项目初始假设，可在界面调整，不代表行业标准。",
        "dimension_labels": DIMENSION_LABELS,
    }


def export_to_dir(
    rows: list[dict], meta: dict, out_dir: Path
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"results_{meta.get('run_id', 'unknown')}.csv"
    meta_path = out_dir / f"results_{meta.get('run_id', 'unknown')}.meta.json"
    csv_path.write_text(rows_to_csv_text(rows), encoding="utf-8-sig")
    meta_path.write_text(
        json.dumps(build_export_meta(meta, rows), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return csv_path, meta_path
