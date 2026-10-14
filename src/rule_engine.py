"""规则引擎：硬性条件过滤 + 软性指标加权排序。

设计原则（对应 docs/01-prompt-revision-notes.md §3–§5）：
- 硬性条件只来自用户显式填写的要求；数据缺失记「无法判定」，不自动淘汰。
- 缺失维度不参与加权，也不视为 0 分；同时输出信息完整度。
- 内容相关性只做词面/类目匹配，语义判断留给 Phase 2 的 AI。
"""

from __future__ import annotations

import re

from .models import (
    DIMENSION_LABELS,
    GOAL_LABELS,
    CreatorAssessment,
    CreatorRecord,
    DimensionScore,
    HardCheckItem,
    HardCheckResult,
    MarketingTask,
    RuleConfig,
)

MARKET_ALIASES: dict[str, set[str]] = {
    "德国": {"de", "germany", "deutschland", "德语区", "german"},
    "美国": {"us", "usa", "united states", "america", "美区", "us market"},
    "西班牙": {"es", "spain", "espana", "españa"},
    "法国": {"fr", "france"},
    "英国": {"uk", "gb", "united kingdom", "britain"},
    "意大利": {"it", "italy", "italia"},
    "日本": {"jp", "japan"},
    "韩国": {"kr", "korea", "south korea"},
    "巴西": {"br", "brazil", "brasil"},
    "墨西哥": {"mx", "mexico"},
    "泰国": {"th", "thailand"},
    "越南": {"vn", "vietnam"},
    "印尼": {"id", "indonesia"},
}

LANGUAGE_ALIASES: dict[str, set[str]] = {
    "德语": {"de", "german", "deutsch"},
    "英语": {"en", "english"},
    "西班牙语": {"es", "spanish", "espanol", "español"},
    "法语": {"fr", "french"},
    "意大利语": {"it", "italian"},
    "日语": {"ja", "jp", "japanese"},
    "韩语": {"ko", "korean"},
    "葡萄牙语": {"pt", "portuguese"},
    "泰语": {"th", "thai"},
    "越南语": {"vi", "vietnamese"},
    "印尼语": {"id", "indonesian"},
}

PLATFORM_ALIASES: dict[str, set[str]] = {
    "tiktok": {"tiktok", "tik tok", "tt"},
    "instagram": {"instagram", "ig", "ins"},
    "youtube": {"youtube", "yt"},
}

STOPWORDS = {
    "the", "and", "for", "with", "of", "to", "a", "an", "in", "on", "is", "are",
    "this", "that", "it", "as", "at", "by", "or", "be", "from", "your", "you",
    "的", "和", "与", "在", "是", "了", "我", "你", "他", "她", "它", "们", "个",
    "很", "都", "也", "就", "还", "而", "及", "等", "这", "那", "内容", "分享",
    "视频", "达人", "产品", "可以", "比较", "非常", "主要",
}


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[\s\u3000]+", " ", text)
    return text


def tokenize(value: object) -> set[str]:
    """抽取关键词：latin 单词 + 中文二字组合。"""
    text = normalize_text(value)
    if not text:
        return set()
    tokens: set[str] = set()
    tokens.update(t for t in re.findall(r"[a-z0-9]{2,}", text) if t not in STOPWORDS)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk_runs:
        if len(run) <= 2:
            if run not in STOPWORDS:
                tokens.add(run)
            continue
        for i in range(len(run) - 1):
            gram = run[i : i + 2]
            if gram not in STOPWORDS:
                tokens.add(gram)
    return tokens


def _alias_group(value: str, table: dict[str, set[str]]) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    for canonical, aliases in table.items():
        if text == canonical or text in aliases:
            return canonical
        for alias in aliases:
            if alias and (alias in text or text in alias):
                return canonical
    return None


def market_score(creator_market: str | None, target_market: str) -> tuple[float | None, str]:
    if not normalize_text(creator_market):
        return None, "market 字段缺失"
    a = _alias_group(creator_market, MARKET_ALIASES)
    b = _alias_group(target_market, MARKET_ALIASES)
    if a and b:
        if a == b:
            return 1.0, f"市场命中：{creator_market} ≈ 目标 {target_market}"
        return 0.0, f"市场不同：{creator_market} vs 目标 {target_market}"
    if normalize_text(creator_market) == normalize_text(target_market):
        return 1.0, f"市场字符串一致：{creator_market}"
    if normalize_text(target_market) in normalize_text(creator_market):
        return 0.5, f"市场部分匹配：{creator_market} 含 {target_market}"
    return 0.0, f"市场未匹配：{creator_market} vs 目标 {target_market}"


def platform_score(creator_platform: str | None, target_platform: str) -> tuple[float | None, str]:
    if not normalize_text(creator_platform):
        return None, "platform 字段缺失"
    a = _alias_group(creator_platform, PLATFORM_ALIASES)
    b = _alias_group(target_platform, PLATFORM_ALIASES)
    if a and b and a == b:
        return 1.0, f"平台命中：{creator_platform}"
    return 0.0, f"平台不同：{creator_platform or '—'} vs 目标 {target_platform}"


def language_score(creator_language: str | None, required_language: str | None) -> tuple[float | None, str]:
    if not required_language:
        return None, "任务未设置内容语言要求"
    if not normalize_text(creator_language):
        return None, "language 字段缺失"
    a = _alias_group(creator_language, LANGUAGE_ALIASES)
    b = _alias_group(required_language, LANGUAGE_ALIASES)
    if a and b and a == b:
        return 1.0, f"语言命中：{creator_language}"
    return 0.0, f"语言不同：{creator_language or '—'} vs 要求 {required_language}"


def compute_engagement(creator: CreatorRecord, config: RuleConfig) -> tuple[float | None, str, str, str]:
    """按优先级给出互动表现：返回 (分数, 口径说明, 方法说明, 缺失原因)。

    1. 数据自带 engagement_rate
    2. (likes + comments) / follower_count
    3. average_views / follower_count（只作表现参考，不是互动率）
    4. 信息不足，不参与加权
    """
    if creator.engagement_rate is not None:
        score = min(creator.engagement_rate / config.engagement_benchmark, 1.0)
        return (
            score,
            creator.engagement_rate_source or "数据文件自带（口径未说明）",
            f"互动率 {creator.engagement_rate:.2%} ÷ 参考基准 {config.engagement_benchmark:.2%}",
            "",
        )
    if creator.likes is not None and creator.comments is not None and creator.follower_count:
        rate = (creator.likes + creator.comments) / creator.follower_count
        score = min(rate / config.engagement_benchmark, 1.0)
        return (
            score,
            "项目内近似口径：(likes + comments) / follower_count",
            f"(likes+comments)/followers = {rate:.2%} ÷ 参考基准 {config.engagement_benchmark:.2%}",
            "",
        )
    if creator.average_views is not None and creator.follower_count:
        rate = creator.average_views / creator.follower_count
        score = min(rate / config.view_rate_benchmark, 1.0)
        return (
            score,
            "降级口径：average_views / follower_count（播放表现，不等同互动率）",
            f"average_views/followers = {rate:.2%} ÷ 参考基准 {config.view_rate_benchmark:.2%}",
            "",
        )
    return None, "", "", "缺少 engagement_rate，且 likes/comments 或 average_views 不完整"


def _content_relevance(creator: CreatorRecord, task: MarketingTask) -> DimensionScore:
    product_tokens = tokenize(task.product_name) | tokenize(task.product_pitch)
    product_tokens |= tokenize(GOAL_LABELS.get(task.marketing_goal, ""))
    category_tokens: set[str] = set()
    for cat in creator.content_categories:
        category_tokens |= tokenize(cat)
    content_tokens = category_tokens | tokenize(creator.recent_content_summary) | tokenize(
        creator.sample_content_text
    )
    used = [
        f
        for f in ("content_categories", "recent_content_summary", "sample_content_text")
        if getattr(creator, f) not in (None, [], "")
    ]
    missing = [
        f
        for f in ("content_categories", "recent_content_summary", "sample_content_text")
        if getattr(creator, f) in (None, [], "")
    ]
    if not content_tokens:
        return DimensionScore(
            name="content_relevance",
            label=DIMENSION_LABELS["content_relevance"],
            score=None,
            used_fields=[],
            method="关键词/类目重叠（确定性匹配）",
            detail="没有可用于比较的内容文本",
            missing=missing,
        )
    overlap = product_tokens & content_tokens
    category_hits = product_tokens & category_tokens
    denom = max(1, min(len(product_tokens), 8))
    score = min(1.0, len(overlap) / denom)
    if category_hits:
        score = min(1.0, score + 0.1)
    return DimensionScore(
        name="content_relevance",
        label=DIMENSION_LABELS["content_relevance"],
        score=score,
        used_fields=used,
        method="关键词/类目重叠（确定性匹配，非语义判断）",
        detail=(
            f"与产品/目标重合的关键词 {len(overlap)} 个"
            + (f"（类目直接命中：{', '.join(sorted(category_hits))}）" if category_hits else "")
        ),
        missing=missing,
    )


def _market_fit(creator: CreatorRecord, task: MarketingTask) -> DimensionScore:
    score, detail = market_score(creator.market, task.target_market)
    return DimensionScore(
        name="market_fit",
        label=DIMENSION_LABELS["market_fit"],
        score=score,
        used_fields=["market"] if creator.market else [],
        method="market 字段与目标市场比对（含常见别名）",
        detail=detail,
        missing=[] if creator.market else ["market"],
    )


def _audience_fit(creator: CreatorRecord, task: MarketingTask) -> DimensionScore:
    if not normalize_text(task.target_audience):
        return DimensionScore(
            name="audience_fit",
            label=DIMENSION_LABELS["audience_fit"],
            score=None,
            method="目标受众关键词与 audience_description 重叠",
            detail="任务未填写目标受众，本维度不参与",
            missing=["target_audience"],
        )
    if not normalize_text(creator.audience_description):
        return DimensionScore(
            name="audience_fit",
            label=DIMENSION_LABELS["audience_fit"],
            score=None,
            method="目标受众关键词与 audience_description 重叠",
            detail="该达人没有受众描述，无法判断",
            missing=["audience_description"],
        )
    target = tokenize(task.target_audience)
    actual = tokenize(creator.audience_description)
    overlap = target & actual
    denom = max(1, min(len(target), 6))
    score = min(1.0, len(overlap) / denom)
    return DimensionScore(
        name="audience_fit",
        label=DIMENSION_LABELS["audience_fit"],
        score=score,
        used_fields=["audience_description"],
        method="目标受众关键词与 audience_description 重叠",
        detail=f"重合关键词 {len(overlap)} 个（{', '.join(sorted(overlap)[:6]) or '无'}）",
    )


def _engagement_dim(creator: CreatorRecord, config: RuleConfig) -> DimensionScore:
    score, source, method, missing_reason = compute_engagement(creator, config)
    if score is None:
        return DimensionScore(
            name="engagement",
            label=DIMENSION_LABELS["engagement"],
            score=None,
            method="按优先级选择互动率口径",
            detail=missing_reason,
            missing=["engagement_rate"],
        )
    return DimensionScore(
        name="engagement",
        label=DIMENSION_LABELS["engagement"],
        score=score,
        used_fields=["engagement_rate"]
        if creator.engagement_rate is not None
        else ["likes", "comments", "follower_count", "average_views"],
        method=method,
        detail=f"口径：{source}",
    )


def _feasibility(creator: CreatorRecord, task: MarketingTask) -> DimensionScore:
    parts: list[tuple[str, float]] = []
    details: list[str] = []
    missing: list[str] = []

    p_score, p_detail = platform_score(creator.platform, task.target_platform)
    if p_score is None:
        missing.append("platform")
    else:
        parts.append(("platform", p_score))
    details.append(p_detail)

    l_score, l_detail = language_score(creator.language, task.required_language)
    if l_score is None:
        if task.required_language and not creator.language:
            missing.append("language")
    else:
        parts.append(("language", l_score))
    details.append(l_detail)

    if creator.collaboration_history:
        details.append(f"合作经历：{creator.collaboration_history}")
    else:
        details.append("没有合作经历记录（信息不足，未计入分数）")

    if not parts:
        return DimensionScore(
            name="feasibility",
            label=DIMENSION_LABELS["feasibility"],
            score=None,
            method="平台匹配与语言要求的平均值",
            detail="；".join(details),
            missing=missing,
        )
    score = sum(v for _, v in parts) / len(parts)
    return DimensionScore(
        name="feasibility",
        label=DIMENSION_LABELS["feasibility"],
        score=score,
        used_fields=[k for k, _ in parts],
        method="平台匹配与语言要求的平均值（合作经历仅作定性提示）",
        detail="；".join(details),
        missing=missing,
    )


def hard_check(creator: CreatorRecord, task: MarketingTask) -> HardCheckResult:
    items: list[HardCheckItem] = []

    if task.min_follower_count is not None:
        if creator.follower_count is None:
            items.append(
                HardCheckItem(
                    rule="最低粉丝量",
                    field="follower_count",
                    actual=None,
                    threshold=f">= {task.min_follower_count}",
                    verdict="unknown",
                    note="数据缺失，无法判定；不会被自动淘汰。",
                )
            )
        elif creator.follower_count >= task.min_follower_count:
            items.append(
                HardCheckItem(
                    rule="最低粉丝量",
                    field="follower_count",
                    actual=str(creator.follower_count),
                    threshold=f">= {task.min_follower_count}",
                    verdict="pass",
                )
            )
        else:
            items.append(
                HardCheckItem(
                    rule="最低粉丝量",
                    field="follower_count",
                    actual=str(creator.follower_count),
                    threshold=f">= {task.min_follower_count}",
                    verdict="fail",
                )
            )

    if task.required_language:
        score, _ = language_score(creator.language, task.required_language)
        if score is None:
            items.append(
                HardCheckItem(
                    rule="内容语言",
                    field="language",
                    actual=None,
                    threshold=task.required_language,
                    verdict="unknown",
                    note="数据缺失，无法判定；进入需核实分组。",
                )
            )
        else:
            items.append(
                HardCheckItem(
                    rule="内容语言",
                    field="language",
                    actual=creator.language,
                    threshold=task.required_language,
                    verdict="pass" if score >= 1.0 else "fail",
                )
            )

    if task.required_market:
        score, _ = market_score(creator.market, task.required_market)
        if score is None:
            items.append(
                HardCheckItem(
                    rule="目标地区",
                    field="market",
                    actual=None,
                    threshold=task.required_market,
                    verdict="unknown",
                    note="数据缺失，无法判定；进入需核实分组。",
                )
            )
        else:
            items.append(
                HardCheckItem(
                    rule="目标地区",
                    field="market",
                    actual=creator.market,
                    threshold=task.required_market,
                    verdict="pass" if score >= 1.0 else "fail",
                )
            )

    if not items:
        return HardCheckResult(items=[], verdict="pass", no_requirement=True)

    verdict = "pass"
    if any(i.verdict == "fail" for i in items):
        verdict = "fail"
    elif any(i.verdict == "unknown" for i in items):
        verdict = "unknown"
    return HardCheckResult(items=items, verdict=verdict)


def evaluate_creator(
    creator: CreatorRecord, task: MarketingTask, config: RuleConfig
) -> CreatorAssessment:
    check = hard_check(creator, task)
    dimensions = [
        _content_relevance(creator, task),
        _market_fit(creator, task),
        _audience_fit(creator, task),
        _engagement_dim(creator, config),
        _feasibility(creator, task),
    ]

    for dim in dimensions:
        dim.weight = float(config.weights.get(dim.name, 0.0))

    scored = [d for d in dimensions if d.score is not None and d.weight > 0]
    weight_sum = sum(d.weight for d in scored)
    if scored and weight_sum > 0:
        weighted = sum((d.score or 0.0) * d.weight for d in scored) / weight_sum
    else:
        weighted = None
    coverage = len(scored) / len([d for d in dimensions if d.weight > 0]) if dimensions else 0.0

    group = {"pass": "qualified", "unknown": "needs_review", "fail": "excluded"}[check.verdict]

    reasons: list[str] = []
    if check.no_requirement:
        reasons.append("任务未设置硬性条件，全部候选都通过硬性检查。")
    for item in check.items:
        label = {"pass": "通过", "fail": "未通过", "unknown": "无法判定"}[item.verdict]
        actual = item.actual if item.actual is not None else "—"
        reasons.append(f"硬性条件「{item.rule}」{label}：{item.field}={actual}（要求 {item.threshold}）")

    for dim in sorted(scored, key=lambda d: (d.score or 0.0) * d.weight, reverse=True):
        reasons.append(f"{dim.label} {dim.score:.2f}：{dim.detail}")
    for dim in dimensions:
        if dim.score is None and dim.missing:
            reasons.append(f"{dim.label} 不参与打分：{dim.detail}")

    missing_all = sorted({m for d in dimensions for m in d.missing} | set(creator.missing_fields))
    if missing_all:
        reasons.append(f"仍缺少的信息：{', '.join(missing_all)}（缺失不会被当作 0 分）")
    reasons.append("内容相关性为词面/类目匹配结果，不代表语义判断；语义分析在 Phase 2 由 AI 完成。")

    return CreatorAssessment(
        creator_id=creator.creator_id,
        display_name=creator.display_name,
        group=group,
        hard_check=check,
        dimensions=dimensions,
        weighted_score=weighted,
        coverage=coverage,
        reasons=reasons,
    )


def evaluate_dataset(
    creators: list[CreatorRecord], task: MarketingTask, config: RuleConfig
) -> list[CreatorAssessment]:
    assessments = [evaluate_creator(c, task, config) for c in creators]
    order = {"qualified": 0, "needs_review": 1, "excluded": 2}
    assessments.sort(
        key=lambda a: (
            order[a.group],
            -(a.weighted_score if a.weighted_score is not None else -1.0),
            -a.coverage,
            a.creator_id,
        )
    )
    rank = 0
    for a in assessments:
        if a.group == "excluded":
            a.rank = None
            continue
        rank += 1
        a.rank = rank
    return assessments


def summarize_assessments(assessments: list[CreatorAssessment]) -> dict[str, int]:
    counts = {"qualified": 0, "needs_review": 0, "excluded": 0}
    for a in assessments:
        counts[a.group] += 1
    counts["unknown_hard_check"] = sum(
        1 for a in assessments if any(i.verdict == "unknown" for i in a.hard_check.items)
    )
    counts["total"] = len(assessments)
    return counts


def top_candidates(assessments: list[CreatorAssessment], top_n: int) -> list[CreatorAssessment]:
    """Phase 2 的 AI 候选池：硬性条件通过者中的前 N 名（默认不含需核实分组）。"""
    return [a for a in assessments if a.group == "qualified"][:top_n]
