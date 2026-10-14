"""规则引擎测试：硬性条件、缺失不惩罚、排序与可解释性。"""

from __future__ import annotations

from src import rule_engine
from src.models import CreatorRecord, MarketingTask, RuleConfig


def make_task(**overrides) -> MarketingTask:
    data = dict(
        product_name="便携电动奶泡杯",
        product_pitch="USB 充电、30 秒打出冷热奶泡，主打租房小厨房",
        target_market="德国",
        target_platform="TikTok",
        marketing_goal="content_seeding",
        target_audience="20-35 岁城市上班族",
        min_follower_count=10_000,
        required_language="德语",
    )
    data.update(overrides)
    return MarketingTask(**data)


def make_creator(**overrides) -> CreatorRecord:
    data = dict(
        creator_id="c1",
        creator_name="达人一",
        platform="TikTok",
        market="德国",
        language="德语",
        follower_count=50_000,
        average_views=60_000,
        likes=3_000,
        comments=200,
        engagement_rate=0.02,
        content_categories=["咖啡", "家用小家电"],
        recent_content_summary="租房咖啡角与奶泡教程",
        sample_content_text="30 秒手打奶泡",
        audience_description="20-35 岁城市租房上班族",
    )
    data.update(overrides)
    return CreatorRecord(**data)


def test_hard_check_pass_and_group() -> None:
    assessment = rule_engine.evaluate_creator(make_creator(), make_task(), RuleConfig())
    assert assessment.hard_check.verdict == "pass"
    assert assessment.group == "qualified"


def test_hard_check_fail_when_below_threshold() -> None:
    assessment = rule_engine.evaluate_creator(
        make_creator(follower_count=5_000), make_task(), RuleConfig()
    )
    assert assessment.hard_check.verdict == "fail"
    assert assessment.group == "excluded"
    assert assessment.rank is None


def test_hard_check_unknown_when_field_missing() -> None:
    assessments = rule_engine.evaluate_dataset(
        [make_creator(follower_count=None)], make_task(), RuleConfig()
    )
    assessment = assessments[0]
    item = [i for i in assessment.hard_check.items if i.field == "follower_count"][0]
    assert item.verdict == "unknown"
    assert assessment.group == "needs_review"
    assert assessment.rank is not None, "无法判定不应被自动淘汰"


def test_no_hard_requirement_passes_everyone() -> None:
    task = make_task(min_follower_count=None, required_language=None)
    assessment = rule_engine.evaluate_creator(make_creator(language=None), task, RuleConfig())
    assert assessment.hard_check.no_requirement
    assert assessment.group == "qualified"


def test_missing_data_is_not_scored_as_zero() -> None:
    creator = make_creator(
        engagement_rate=None, likes=None, comments=None, average_views=None, audience_description=None
    )
    assessment = rule_engine.evaluate_creator(creator, make_task(), RuleConfig())
    engagement = [d for d in assessment.dimensions if d.name == "engagement"][0]
    audience = [d for d in assessment.dimensions if d.name == "audience_fit"][0]
    assert engagement.score is None
    assert audience.score is None
    assert assessment.weighted_score is not None
    assert 0 < assessment.coverage < 1
    assert any("不参与打分" in r for r in assessment.reasons)


def test_weighted_score_renormalizes_over_available_dimensions() -> None:
    full = rule_engine.evaluate_creator(make_creator(), make_task(), RuleConfig())
    partial = rule_engine.evaluate_creator(
        make_creator(
            engagement_rate=None, likes=None, comments=None, average_views=None
        ),
        make_task(),
        RuleConfig(),
    )
    assert full.weighted_score is not None and partial.weighted_score is not None
    assert partial.coverage < full.coverage
    assert partial.weighted_score > 0.3, "缺失维度不应把总分拉向 0"


def test_market_and_language_mismatch_lower_scores() -> None:
    matched = rule_engine.evaluate_creator(make_creator(), make_task(), RuleConfig())
    unmatched = rule_engine.evaluate_creator(
        make_creator(market="美国", language="英语"), make_task(required_language=None), RuleConfig()
    )
    assert unmatched.weighted_score is not None and matched.weighted_score is not None
    assert unmatched.weighted_score < matched.weighted_score


def test_content_relevance_is_lexical_and_documented() -> None:
    relevant = rule_engine.evaluate_creator(make_creator(), make_task(), RuleConfig())
    irrelevant = rule_engine.evaluate_creator(
        make_creator(
            content_categories=["宠物"],
            recent_content_summary="猫咪日常",
            sample_content_text="养猫经验",
        ),
        make_task(),
        RuleConfig(),
    )
    rel = [d for d in relevant.dimensions if d.name == "content_relevance"][0]
    irr = [d for d in irrelevant.dimensions if d.name == "content_relevance"][0]
    assert rel.score is not None and irr.score is not None
    assert rel.score > irr.score
    assert "非语义判断" in rel.method


def test_engagement_priority_and_source_label() -> None:
    creator = make_creator(engagement_rate=None, likes=1000, comments=100)
    config = RuleConfig()
    score, source, method, missing = rule_engine.compute_engagement(creator, config)
    assert score is not None and "likes" in source
    creator_no_social = make_creator(engagement_rate=None, likes=None, comments=None)
    score2, source2, _, _ = rule_engine.compute_engagement(creator_no_social, config)
    assert score2 is not None and "average_views" in source2
    creator_empty = make_creator(
        engagement_rate=None, likes=None, comments=None, average_views=None
    )
    score3, _, _, missing3 = rule_engine.compute_engagement(creator_empty, config)
    assert score3 is None and missing3


def test_dataset_ranking_groups_and_top_candidates() -> None:
    creators = [
        make_creator(creator_id="best", follower_count=90_000),
        make_creator(creator_id="mid", follower_count=20_000, engagement_rate=0.005),
        make_creator(creator_id="unknown", follower_count=None),
        make_creator(creator_id="out", follower_count=1_000),
    ]
    assessments = rule_engine.evaluate_dataset(creators, make_task(), RuleConfig())
    order = [a.creator_id for a in assessments]
    assert order.index("best") < order.index("mid")
    assert order[-1] == "out"
    counts = rule_engine.summarize_assessments(assessments)
    assert counts == {
        "qualified": 2,
        "needs_review": 1,
        "excluded": 1,
        "unknown_hard_check": 1,
        "total": 4,
    }
    top = rule_engine.top_candidates(assessments, 1)
    assert [a.creator_id for a in top] == ["best"]


def test_every_assessment_explains_itself() -> None:
    assessment = rule_engine.evaluate_creator(make_creator(), make_task(), RuleConfig())
    assert len(assessment.reasons) >= 5
    for dim in assessment.dimensions:
        assert dim.method
        assert dim.detail
