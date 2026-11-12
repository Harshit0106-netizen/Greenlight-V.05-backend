from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.finding import FindingType, Severity
from app.schemas.brand_kit import SourceKind


class PatternSource(BaseModel):
    kind: SourceKind
    id: str
    version: str | None = None
    title: str


class PatternExample(BaseModel):
    review_id: str
    quote: str


class PatternOutcomes(BaseModel):
    edited: int
    disputed: int
    accepted: int
    open: int


class PatternRecommendation(BaseModel):
    action: Literal["add_guidance", "clarify_guideline", "add_fact", "review_rule", "update_template"]
    text: str


class Pattern(BaseModel):
    id: str
    type: FindingType
    title: str
    severity: Severity
    occurrences: int
    reviews_affected: int
    creators_affected: int
    first_seen: datetime
    last_seen: datetime
    trend: Literal["new", "rising", "steady", "falling"]
    dispute_rate: float
    outcomes: PatternOutcomes
    source: PatternSource | None
    examples: list[PatternExample]
    recommendation: PatternRecommendation


class Hotspot(BaseModel):
    source_id: str
    title: str
    kind: SourceKind
    findings: int
    reviews_affected: int
    dispute_rate: float


class CategoryCount(BaseModel):
    type: FindingType
    findings: int
    reviews_affected: int
    recurring_patterns: int


class PatternsOut(BaseModel):
    window_days: int
    generated_at: datetime
    reviews_analyzed: int
    findings_analyzed: int
    patterns: list[Pattern]
    hotspots: list[Hotspot]
    categories: list[CategoryCount]
