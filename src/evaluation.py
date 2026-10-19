"""离线评估：在同一批固定案例上比较「纯规则」与「规则 + AI」。

诚实声明（会原样写进报告）：
- 12 个案例与参考标签由 AI 起草，属于项目内假设标签，需要人工逐条确认；
- 样本很小，不构成统计显著的结论；
- 如果 AI 的表现不如规则，报告会如实呈现，不做挑选。

用法：
    python -m src.evaluation            # 只跑规则（不需要 API Key）
    python -m src.evaluation --ai       # 同时跑 AI（需要配置 Key，会产生调用）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import ai_analyzer
from .ai_client import DeepSeekClient, LLMClient
from .data_loader import CANONICAL_FIELDS
from .models import (
    CreatorAssessment,
    CreatorRecord,
    MarketingTask,
    RuleConfig,
    AIAnalysisRecord,
)
from .rule_engine import evaluate_creator
from .snapshot import PROJECT_ROOT

EVAL_PATH = PROJECT_ROOT / "data" / "evaluation" / "evaluation_cases.json"

# 规则方案判定为「推荐」的阈值。这是一个项目内假设，不是行业标准。
RULE_RECOMMEND_THRESHOLD = 0.60

LABEL_CAVEAT = (
    "案例与参考标签由 AI 起草，属于项目内假设标签，需人工逐条确认；"
    "样本量小，不构成统计显著的结论。"
)


def load_cases(path: Path | None = None) -> dict:
    target = path or EVAL_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _creator_from_dict(data: dict) -> CreatorRecord:
    payload = {k: v for k, v in data.items() if k in CreatorRecord.model_fields}
    missing = [
        field
        for field in CANONICAL_FIELDS
        if field != "creator_id" and payload.get(field) in (None, "", [])
    ]
    payload.setdefault("missing_fields", missing)
    return CreatorRecord(**payload)


def _task_from_dict(base: dict, overrides: dict | None = None) -> MarketingTask:
    data = {**base, **(overrides or {})}
    return MarketingTask(**data)


def decide_by_rules(
    assessment: CreatorAssessment, threshold: float = RULE_RECOMMEND_THRESHOLD
) -> str:
    """纯规则方案的结论：recommend / not_recommend / insufficient。"""
    if assessment.hard_check.verdict == "fail":
        return "not_recommend"
    if assessment.hard_check.verdict == "unknown":
        return "insufficient"
    if assessment.weighted_score is None:
        return "insufficient"
    return "recommend" if assessment.weighted_score >= threshold else "not_recommend"


def decide_by_rule_plus_ai(
    record: AIAnalysisRecord | None, assessment: CreatorAssessment
) -> str:
    """方案 B：**相同的基础筛选结果** + AI 语义判断。

    这一点很关键：如果丢掉硬性过滤，方案 B 就变成了「纯 AI」，对比不公平。
    硬性条件未通过 → 直接不推荐；无法判定 → 信息不足；两者都不由 AI 覆盖。
    """
    if assessment.hard_check.verdict == "fail":
        return "not_recommend"
    if assessment.hard_check.verdict == "unknown":
        return "insufficient"
    if record is None:
        return "not_run"
    if record.status == "failed" or record.analysis is None:
        return "ai_failed"
    analysis = record.analysis
    verdicts = (
        analysis.content_relevance.verdict,
        analysis.goal_fit.verdict,
        analysis.audience_fit.verdict,
    )
    if analysis.analysis_status == "insufficient_info" or "unknown" in verdicts:
        return "insufficient"
    if (
        analysis.content_relevance.verdict in ("strong", "moderate")
        and analysis.goal_fit.verdict in ("strong", "moderate")
    ):
        return "recommend"
    return "not_recommend"


DECISION_LABELS = {
    "recommend": "推荐",
    "not_recommend": "不推荐",
    "insufficient": "信息不足",
    "not_run": "未运行",
    "ai_failed": "AI 失败",
}


def run_evaluation(
    client: LLMClient | None = None,
    threshold: float = RULE_RECOMMEND_THRESHOLD,
    template: str | None = None,
    save_path: Path | None = None,
) -> dict:
    payload = load_cases()
    base_task = payload["task"]
    template = template or ai_analyzer.load_prompt()
    rows: list[dict] = []

    for case in payload["cases"]:
        creator = _creator_from_dict(case["creator"])
        task = _task_from_dict(base_task, case.get("task_overrides"))
        assessment = evaluate_creator(creator, task, RuleConfig())
        rules_decision = decide_by_rules(assessment, threshold)

        record = None
        if client is not None:
            record = ai_analyzer.analyze_one(
                creator, assessment, task, client, template=template
            )
        ai_decision = decide_by_rule_plus_ai(record, assessment)

        rows.append(
            {
                "case_id": case["case_id"],
                "focus": case["focus"],
                "expected": case["expected"],
                "rules_decision": rules_decision,
                "rule_score": assessment.weighted_score,
                "rule_coverage": assessment.coverage,
                "ai_decision": ai_decision,
                "ai_status": record.status if record else "not_run",
                "ai_error": record.error_message if record else None,
                "ai_evidence_problems": record.evidence_problems if record else [],
                "ai_numeric_conflicts": record.numeric_conflicts if record else [],
                "reference_note": case["reference_note"],
            }
        )

    report = {
        "version": payload.get("version"),
        "case_count": len(rows),
        "label_source": payload.get("label_source"),
        "caveat": LABEL_CAVEAT,
        "threshold": threshold,
        "ai_run": client is not None,
        "rows": rows,
        "summary": _summarize(rows, ai_run=client is not None),
    }
    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def _summarize(rows: list[dict], ai_run: bool) -> dict:
    rules_correct = sum(1 for r in rows if r["rules_decision"] == r["expected"])
    summary: dict = {
        "rules_correct": rules_correct,
        "rules_total": len(rows),
        "rules_errors": [
            r["case_id"] for r in rows if r["rules_decision"] != r["expected"]
        ],
    }
    if not ai_run:
        summary["ai"] = "未运行"
        return summary

    ai_correct = sum(1 for r in rows if r["ai_decision"] == r["expected"])
    summary.update(
        {
            "ai_correct": ai_correct,
            "ai_errors": [r["case_id"] for r in rows if r["ai_decision"] != r["expected"]],
            "incremental": [
                r["case_id"]
                for r in rows
                if r["rules_decision"] != r["expected"] and r["ai_decision"] == r["expected"]
            ],
            "new_errors": [
                r["case_id"]
                for r in rows
                if r["rules_decision"] == r["expected"] and r["ai_decision"] != r["expected"]
            ],
            "both_wrong": [
                r["case_id"]
                for r in rows
                if r["rules_decision"] != r["expected"] and r["ai_decision"] != r["expected"]
            ],
            "ai_failed": [r["case_id"] for r in rows if r["ai_status"] == "failed"],
            "evidence_problems": sum(1 for r in rows if r["ai_evidence_problems"]),
            "numeric_conflicts": sum(1 for r in rows if r["ai_numeric_conflicts"]),
        }
    )
    return summary


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# 离线评估报告",
        "",
        f"- 案例数：{report['case_count']}",
        f"- 规则推荐阈值：{report['threshold']:.2f}（项目内假设）",
        f"- 标签来源：{report['label_source']}",
        f"- 局限：{report['caveat']}",
        "",
        "## 逐案结果",
        "",
        "| 案例 | 考察点 | 参考判定 | 规则判定 | 规则分 | AI 判定 |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        score = f"{r['rule_score']:.3f}" if r["rule_score"] is not None else "—"
        lines.append(
            f"| {r['case_id']} | {r['focus']} | "
            f"{DECISION_LABELS[r['expected']]} | {DECISION_LABELS[r['rules_decision']]} | "
            f"{score} | {DECISION_LABELS.get(r['ai_decision'], r['ai_decision'])} |"
        )

    lines += [
        "",
        "## 汇总",
        "",
        f"- 纯规则：{s['rules_correct']} / {s['rules_total']} 与参考判定一致",
    ]
    if report["ai_run"]:
        lines += [
            f"- 规则 + AI：{s['ai_correct']} / {s['rules_total']} 与参考判定一致",
            f"- 增量修正（规则错、AI 对）：{len(s['incremental'])} 个"
            + (f"（{', '.join(s['incremental'])}）" if s["incremental"] else ""),
            f"- 新增错误（规则对、AI 错）：{len(s['new_errors'])} 个"
            + (f"（{', '.join(s['new_errors'])}）" if s["new_errors"] else ""),
            f"- 两者都错：{len(s['both_wrong'])} 个"
            + (f"（{', '.join(s['both_wrong'])}）" if s["both_wrong"] else ""),
            f"- AI 调用失败：{len(s['ai_failed'])} 个",
            f"- 证据无法核对：{s['evidence_problems']} 条；出现可疑数字：{s['numeric_conflicts']} 条",
        ]
    else:
        lines.append("- AI：未运行（需要配置 API Key 后加 `--ai` 重新评估）")

    lines += [
        "",
        "> 注意：以上是 12 个案例的小样本结果，不能当作行业准确率，也不代表真实业务收益。",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="在固定案例集上运行离线评估")
    parser.add_argument("--ai", action="store_true", help="同时运行 AI（会调用 API 并产生费用）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    args = parser.parse_args()

    client = None
    if args.ai:
        client = DeepSeekClient.from_env()
        if client is None:
            raise SystemExit("未配置 DEEPSEEK_API_KEY，无法运行 --ai。")

    report = run_evaluation(client=client, save_path=PROJECT_ROOT / "runtime" / "eval_last_report.json")
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_markdown(report))


if __name__ == "__main__":
    main()
