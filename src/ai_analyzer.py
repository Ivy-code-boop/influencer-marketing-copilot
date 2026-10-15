"""AI 语义分析：组装 prompt、调用模型、校验结构、核对证据、缓存去重、单条重试。

设计要点：
- 只分析通过硬性条件的候选人，默认前 N 名（预算闸门）。
- 模型返回必须过 Pydantic 校验；证据必须能在原始字段里逐字找到。
- 失败一律如实记录为 failed，绝不伪造结论；单条可重试、可命中缓存。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .ai_client import AIError, LLMClient, scrub
from .models import (
    AIAnalysis,
    AIAnalysisRecord,
    CreatorAssessment,
    CreatorRecord,
    MarketingTask,
)
from .snapshot import PROJECT_ROOT, RUNTIME_DIR

PROMPT_VERSION = "creator_analysis_v1"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "creator_analysis_v1.md"
DEFAULT_TOP_N = 10
MAX_TOP_N = 30

SYSTEM_PROMPT = (
    "你是海外达人营销的分析助手。"
    "你只能依据用户提供的 JSON 数据作答，不得引入外部知识、不得编造数字或经历。"
    "信息不足时如实说明，不要猜测。"
    "只输出一个 JSON 对象，不要输出任何解释文字或 Markdown 代码块标记。"
)

# evidence.field 必须是输入里真实存在的字段
EVIDENCE_FIELDS: tuple[str, ...] = (
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
    "data_quality_notes",
)

TEXTUAL_FIELDS: tuple[str, ...] = (
    "creator_name",
    "platform",
    "market",
    "language",
    "content_categories",
    "recent_content_summary",
    "sample_content_text",
    "audience_description",
    "collaboration_history",
    "data_quality_notes",
)


def load_prompt(path: Path | None = None) -> str:
    target = path or PROMPT_PATH
    return target.read_text(encoding="utf-8")


def creator_public_view(creator: CreatorRecord) -> dict:
    """只把可给的字段交给模型，并显式标注缺失（模型不必猜）。"""
    data: dict[str, object] = {"creator_id": creator.creator_id}
    for field in EVIDENCE_FIELDS:
        if field == "creator_id":
            continue
        value = getattr(creator, field, None)
        data[field] = value
    data["missing_fields"] = list(creator.missing_fields)
    return data


def build_payload(
    task: MarketingTask, pairs: list[tuple[CreatorRecord, CreatorAssessment]]
) -> dict:
    return {
        "task": {
            "product_name": task.product_name,
            "product_pitch": task.product_pitch,
            "target_market": task.target_market,
            "target_platform": task.target_platform,
            "marketing_goal": task.marketing_goal,
            "target_audience": task.target_audience,
            "collaboration_type": task.collaboration_type,
            "budget_range": task.budget_range,
        },
        "creators": [
            {
                **creator_public_view(creator),
                "hard_check": {
                    "verdict": assessment.hard_check.verdict,
                    "items": [item.model_dump() for item in assessment.hard_check.items],
                },
                "dimensions_without_data": sorted(
                    {m for d in assessment.dimensions for m in d.missing}
                ),
            }
            for creator, assessment in pairs
        ],
    }


def render_prompt(template: str, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if "{{payload}}" in template:
        return template.replace("{{payload}}", body)
    return f"{template}\n\n{body}"


def normalize_for_match(text: object) -> str:
    if text is None:
        return ""
    return re.sub(r"[\s\u3000“”\"'`]+", "", str(text)).lower()


def _check_evidence(items, creator: CreatorRecord, label: str, problems: list[str]) -> None:
    for ev in items:
        if ev.field not in EVIDENCE_FIELDS:
            problems.append(f"{label}：引用了输入里不存在的字段「{ev.field}」")
            continue
        raw = getattr(creator, ev.field, None)
        if raw in (None, "", []):
            problems.append(f"{label}：引用了空字段「{ev.field}」")
            continue
        haystack = (
            "、".join(raw) if isinstance(raw, list) else str(raw)
        )
        if normalize_for_match(ev.quote) not in normalize_for_match(haystack):
            snippet = ev.quote[:24]
            problems.append(f"{label}：证据原文在「{ev.field}」中找不到：{snippet}")


def validate_evidence(analysis: AIAnalysis, creator: CreatorRecord) -> list[str]:
    """核对每条证据是否能落在原始数据上。返回问题清单（空表示全部可核对）。"""
    problems: list[str] = []
    _check_evidence(analysis.content_relevance.evidence, creator, "内容相关性", problems)
    _check_evidence(analysis.goal_fit.evidence, creator, "目标匹配", problems)
    _check_evidence(analysis.audience_fit.evidence, creator, "受众匹配", problems)
    for risk in analysis.risks:
        _check_evidence(risk.evidence, creator, f"风险（{risk.type}）", problems)
    return problems


def creator_number_strings(creator: CreatorRecord) -> set[str]:
    """该达人数据里出现过的全部数字串，用于发现模型编造的数字。"""
    numbers: set[str] = set()
    for field in ("follower_count", "average_views", "likes", "comments"):
        value = getattr(creator, field, None)
        if value is not None:
            numbers.add(str(int(value)))
    if creator.engagement_rate is not None:
        numbers.add(re.sub(r"\D", "", f"{creator.engagement_rate}"))
        numbers.add(re.sub(r"\D", "", f"{creator.engagement_rate * 100:.2f}"))
    for field in TEXTUAL_FIELDS:
        value = getattr(creator, field, None)
        if value in (None, "", []):
            continue
        text = "、".join(value) if isinstance(value, list) else str(value)
        for token in re.findall(r"\d[\d,]*", text):
            numbers.add(re.sub(r"\D", "", token))
    return {n for n in numbers if n}


def detect_numeric_conflicts(analysis: AIAnalysis, creator: CreatorRecord) -> list[str]:
    """启发式检查：结论里出现的长数字是否能在输入中找到。会有误报，故只做标记。"""
    texts: list[str] = [
        analysis.content_relevance.reason,
        analysis.goal_fit.reason,
        analysis.audience_fit.reason,
        *analysis.recommendation_reasons,
        *analysis.mismatches,
        *[risk.detail for risk in analysis.risks],
    ]
    allowed = creator_number_strings(creator)
    conflicts: list[str] = []
    for text in texts:
        for token in re.findall(r"\d[\d,\.]*", text or ""):
            digits = re.sub(r"\D", "", token)
            if len(digits) < 3:
                continue
            if not any(digits in candidate for candidate in allowed):
                conflicts.append(f"结论里出现了输入中没有的数字：{token}")
    seen: list[str] = []
    for item in conflicts:
        if item not in seen:
            seen.append(item)
    return seen[:5]


def parse_analysis(data: dict, creator_id: str) -> AIAnalysis:
    payload: object = data
    if isinstance(data.get("results"), list):
        matches = [
            item
            for item in data["results"]
            if isinstance(item, dict) and str(item.get("creator_id")) == creator_id
        ]
        if not matches:
            raise AIError("format", f"返回结果里没有 creator_id={creator_id} 的记录。")
        payload = matches[0]
    if not isinstance(payload, dict):
        raise AIError("format", "返回结构不是单个对象，也不含 results 列表。")
    payload = dict(payload)
    payload.setdefault("creator_id", creator_id)
    if str(payload.get("creator_id")) != creator_id:
        raise AIError("format", f"返回的 creator_id（{payload.get('creator_id')}）与请求不一致。")
    try:
        return AIAnalysis.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise AIError(
            "validation", f"结构校验失败：{location} {first.get('msg', '')}".strip()
        ) from exc


class AnalysisCache:
    """本地缓存，避免重复调用产生不必要的费用。文件在 runtime/ 下（已被 .gitignore 忽略）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (RUNTIME_DIR / "ai_cache.json")
        self._data: dict[str, dict] = {}
        self.write_error: str | None = None
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    @staticmethod
    def make_key(
        creator_id: str,
        data_hash: str,
        task_hash_value: str,
        prompt_version: str,
        model: str,
    ) -> str:
        raw = "|".join([creator_id, data_hash, task_hash_value, prompt_version, model])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        # 缓存只是省钱的优化：写不进去（权限、磁盘等）也不能让本次分析失败
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.write_error = None
        except OSError as exc:
            self.write_error = f"{type(exc).__name__}: {exc}"

    def __len__(self) -> int:
        return len(self._data)


def analyze_one(
    creator: CreatorRecord,
    assessment: CreatorAssessment,
    task: MarketingTask,
    client: LLMClient,
    template: str | None = None,
    cache: AnalysisCache | None = None,
    data_hash: str = "",
    task_hash_value: str = "",
    force: bool = False,
) -> AIAnalysisRecord:
    model = getattr(client, "model", "unknown")
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    cache_key = AnalysisCache.make_key(
        creator.creator_id, data_hash, task_hash_value, PROMPT_VERSION, model
    ) if cache is not None else None

    if cache is not None and cache_key is not None and not force:
        hit = cache.get(cache_key)
        if hit:
            record = AIAnalysisRecord(**hit)
            record.from_cache = True
            return record

    try:
        prompt = render_prompt(template or load_prompt(), build_payload(task, [(creator, assessment)]))
        result = client.complete_json(SYSTEM_PROMPT, prompt)
        analysis = parse_analysis(result.data or {}, creator.creator_id)
        problems = validate_evidence(analysis, creator)
        conflicts = detect_numeric_conflicts(analysis, creator)
        record = AIAnalysisRecord(
            creator_id=creator.creator_id,
            status=analysis.analysis_status,
            analysis=analysis,
            attempts=result.attempts,
            model_returned=result.model,
            prompt_version=PROMPT_VERSION,
            created_at=created_at,
            evidence_problems=problems,
            numeric_conflicts=conflicts,
            error_kind="evidence" if problems else None,
            error_message="；".join(problems) if problems else None,
        )
    except AIError as exc:
        record = AIAnalysisRecord(
            creator_id=creator.creator_id,
            status="failed",
            error_kind=exc.kind,
            error_message=scrub(str(exc)),
            attempts=exc.attempts,
            prompt_version=PROMPT_VERSION,
            created_at=created_at,
        )
    except Exception as exc:  # noqa: BLE001 —— 任何未预期错误都必须记录，不许静默
        record = AIAnalysisRecord(
            creator_id=creator.creator_id,
            status="failed",
            error_kind="unknown",
            error_message=scrub(f"{type(exc).__name__}: {exc}"),
            prompt_version=PROMPT_VERSION,
            created_at=created_at,
        )

    if cache is not None and cache_key is not None and record.status != "failed":
        cache.set(cache_key, record.model_dump())
    return record


def estimate_requests(
    pairs: list[tuple[CreatorRecord, CreatorAssessment]],
    data_hash: str,
    task_hash_value: str,
    model: str,
    cache: AnalysisCache | None = None,
) -> dict:
    cached = 0
    if cache is not None:
        for creator, _ in pairs:
            key = AnalysisCache.make_key(
                creator.creator_id, data_hash, task_hash_value, PROMPT_VERSION, model
            )
            if cache.get(key):
                cached += 1
    return {
        "candidates": len(pairs),
        "cached": cached,
        "to_call": len(pairs) - cached,
        "model": model,
        "prompt_version": PROMPT_VERSION,
    }


def analyze_candidates(
    pairs: list[tuple[CreatorRecord, CreatorAssessment]],
    task: MarketingTask,
    client: LLMClient,
    template: str | None = None,
    cache: AnalysisCache | None = None,
    data_hash: str = "",
    task_hash_value: str = "",
    force: bool = False,
) -> list[AIAnalysisRecord]:
    prompt = template or load_prompt()
    return [
        analyze_one(
            creator,
            assessment,
            task,
            client,
            template=prompt,
            cache=cache,
            data_hash=data_hash,
            task_hash_value=task_hash_value,
            force=force,
        )
        for creator, assessment in pairs
    ]
