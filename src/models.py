"""Pydantic 数据模型。

命名约定：字段名一律英文小写下划线；界面文案中文；业务术语保留英文原词。
缺失字段一律用 None / 空列表表示，绝不填 0 冒充真实值。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

MarketingGoal = Literal[
    "brand_awareness", "content_seeding", "product_education", "sales_conversion"
]
GOAL_LABELS: dict[str, str] = {
    "brand_awareness": "品牌曝光",
    "content_seeding": "内容种草",
    "product_education": "产品教育",
    "sales_conversion": "销售转化",
}

Verdict = Literal["pass", "fail", "unknown"]
Group = Literal["qualified", "needs_review", "excluded"]
ReviewStatus = Literal["pending", "contact", "hold", "need_info"]
REVIEW_STATUS_LABELS: dict[str, str] = {
    "pending": "待审核",
    "contact": "建议联系",
    "hold": "暂不联系",
    "need_info": "需要补充信息",
}


class CreatorRecord(BaseModel):
    """一条达人记录。字段缺失即为 None，不参与打分，也不视为 0 分。"""

    creator_id: str
    creator_name: Optional[str] = None
    platform: Optional[str] = None
    market: Optional[str] = None
    language: Optional[str] = None
    follower_count: Optional[int] = None
    average_views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    engagement_rate: Optional[float] = None
    engagement_rate_source: Optional[str] = None
    content_categories: list[str] = Field(default_factory=list)
    recent_content_summary: Optional[str] = None
    sample_content_text: Optional[str] = None
    audience_description: Optional[str] = None
    collaboration_history: Optional[str] = None
    data_source: Optional[str] = None
    data_updated_at: Optional[str] = None
    data_quality_notes: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.creator_name or self.creator_id


class MarketingTask(BaseModel):
    """营销任务配置。必填字段见 docs/01-prompt-revision-notes.md §2。"""

    product_name: str
    product_pitch: str
    target_market: str
    target_platform: str
    marketing_goal: MarketingGoal
    target_audience: Optional[str] = None
    collaboration_type: Optional[str] = None
    budget_range: Optional[str] = None
    min_follower_count: Optional[int] = None
    required_language: Optional[str] = None
    required_market: Optional[str] = None

    def hard_requirements(self) -> list[str]:
        """列出用户显式填写的硬性要求（未填写的条目不会启用）。"""
        active: list[str] = []
        if self.min_follower_count is not None:
            active.append("min_follower_count")
        if self.required_language:
            active.append("required_language")
        if self.required_market:
            active.append("required_market")
        return active


class RuleConfig(BaseModel):
    """规则配置。权重是项目初始假设，可调，不是行业标准。"""

    version: str = "rules_v1"
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "content_relevance": 0.30,
            "market_fit": 0.20,
            "engagement": 0.20,
            "audience_fit": 0.15,
            "feasibility": 0.15,
        }
    )
    engagement_benchmark: float = 0.03
    view_rate_benchmark: float = 0.15
    weights_note: str = "项目初始假设，可在界面调整；不代表行业标准"


DIMENSION_LABELS: dict[str, str] = {
    "content_relevance": "内容相关性（词面/类目）",
    "market_fit": "目标市场匹配",
    "audience_fit": "受众匹配",
    "engagement": "互动表现",
    "feasibility": "合作可行性",
}


class HardCheckItem(BaseModel):
    rule: str
    field: str
    actual: Optional[str]
    threshold: str
    verdict: Verdict
    note: str = ""


class HardCheckResult(BaseModel):
    items: list[HardCheckItem] = Field(default_factory=list)
    verdict: Verdict = "pass"
    no_requirement: bool = False


class DimensionScore(BaseModel):
    name: str
    label: str
    score: Optional[float] = None
    weight: float = 0.0
    used_fields: list[str] = Field(default_factory=list)
    method: str = ""
    detail: str = ""
    missing: list[str] = Field(default_factory=list)


class CreatorAssessment(BaseModel):
    creator_id: str
    display_name: str
    group: Group
    hard_check: HardCheckResult
    dimensions: list[DimensionScore] = Field(default_factory=list)
    weighted_score: Optional[float] = None
    coverage: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    rank: Optional[int] = None


class ReviewDecision(BaseModel):
    creator_id: str
    status: ReviewStatus = "pending"
    note: str = ""


class ValidationIssue(BaseModel):
    level: Literal["error", "warning", "info"]
    row: Optional[int] = None
    field: Optional[str] = None
    message: str
    suggestion: str = ""


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)
    row_count: int = 0
    mapped_fields: dict[str, str] = Field(default_factory=dict)
    unknown_fields: list[str] = Field(default_factory=list)
    encoding_used: str = ""
    duplicate_ids: list[str] = Field(default_factory=list)
    droppable: bool = False

    def by_level(self, level: str) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == level]

    @property
    def has_blocking_error(self) -> bool:
        return any(i.level == "error" for i in self.issues)


class Dataset(BaseModel):
    source_name: str
    origin: Literal["demo", "upload", "evaluation"]
    creators: list[CreatorRecord] = Field(default_factory=list)
    report: ValidationReport = Field(default_factory=ValidationReport)
    data_hash: str = ""
