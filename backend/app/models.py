from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Direction = Literal["bullish", "bearish", "either"]
ZoneMode = Literal["full_wick", "body_to_wick", "half_wick"]
ResearchMode = Literal["scan", "backtest", "research"]
ALLOWED_TFS = {"1m", "3m", "5m", "15m", "30m", "1h", "4h"}


class StrategyInterpretRequest(BaseModel):
    text: str = Field(..., min_length=2)


class StrategyNode(BaseModel):
    type: str
    timeframe: str | None = None
    direction: Direction = "either"
    params: dict[str, Any] = Field(default_factory=dict)


class StrategySpec(BaseModel):
    school: str = "smc"
    strategy_key: str = "smc_core"
    symbol: str = "XAUUSD"
    market_label: str = "Gold"
    timeframe: str = "1h"
    lookback_days: int = 30
    mode: ResearchMode = "research"
    primary_timeframe: str = "1h"
    execution_timeframe: str = "15m"
    direction: Direction = "either"
    sequence: list[StrategyNode] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    unsupported_phrases: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    parser_source: str = "heuristic"
    confidence: float = 0.0
    preferences_applied: list[str] = Field(default_factory=list)

    @field_validator("school", "strategy_key")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("timeframe", "primary_timeframe", "execution_timeframe")
    @classmethod
    def validate_timeframe(cls, value: str) -> str:
        if value not in ALLOWED_TFS:
            raise ValueError(f"Unsupported timeframe: {value}")
        return value

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        if value < 1:
            return 1
        if value > 3650:
            return 3650
        return value


class StrategyInterpretResponse(BaseModel):
    understood: StrategySpec
    preview_text: str
    follow_up_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class StrategyCorrectRequest(BaseModel):
    strategy: StrategySpec
    correction_text: str = Field(..., min_length=2)


class StrategySaveRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    strategy: StrategySpec


class SavedStrategySummary(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    primary_timeframe: str
    execution_timeframe: str
    direction: Direction
    steps: list[str] = Field(default_factory=list)


class SavedStrategyDetail(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    strategy: StrategySpec


class StrategySaveResponse(BaseModel):
    saved: SavedStrategySummary


class BacktestRange(BaseModel):
    start: str | None = None
    end: str | None = None
    label: str | None = None


class ResearchRunRequest(BaseModel):
    strategy: StrategySpec
    backtest_range: BacktestRange | None = None
    source_lookback: int = 12
    zone_retest_window_bars: int = 72
    ltf_confirmation_window_bars: int = 24
    mss_lookback: int = 6
    outcome_window_bars: int = 36
    max_setups: int = 50


class ChartContextRequest(BaseModel):
    symbol: str
    timeframe: str
    start_time: str
    end_time: str


class SetupResult(BaseModel):
    setup_id: str
    htf_time: str
    source_type: str
    source_direction: Direction
    session_label: str
    zone_low: float
    zone_high: float
    entry_reference_price: float
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_points: float | None = None
    reward_points: float | None = None
    rr_ratio: float | None = None
    trade_status: str = "planned"
    execution_model: str | None = None
    execution_notes: list[str] = Field(default_factory=list)
    source_open: float | None = None
    source_high: float | None = None
    source_low: float | None = None
    source_close: float | None = None
    source_range_points: float = 0.0
    ltf_retest_time: str | None = None
    sweep_time: str | None = None
    equal_level_time: str | None = None
    displacement_time: str | None = None
    breaker_time: str | None = None
    mss_time: str | None = None
    fvg_time: str | None = None
    premium_discount_state: str | None = None
    outcome_label: str
    outcome_move_points: float
    adverse_move_points: float
    matched_steps_count: int = 0
    missing_steps: list[str] = Field(default_factory=list)
    quality_score: float = 0.0
    quality_label: str = "average"
    confirmation_chain: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    chart_focus: dict[str, Any] = Field(default_factory=dict)
    explanation: list[str] = Field(default_factory=list)


class ResearchRunResponse(BaseModel):
    run_id: str | None = None
    strategy_summary: str
    human_summary: str = ""
    supported: bool
    unsupported_reasons: list[str] = Field(default_factory=list)
    total_candidates: int
    total_qualified: int
    setups: list[SetupResult] = Field(default_factory=list)
    executed_plan: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    search_scope: dict[str, Any] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResearchRunFile(BaseModel):
    id: str
    created_at: datetime
    request: ResearchRunRequest
    response: ResearchRunResponse


class ResearchRunSummary(BaseModel):
    id: str
    created_at: str
    summary: str
    total_qualified: int
    avg_quality_score: float = 0.0


class StrategyFile(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    strategy: StrategySpec


class PreferenceProfile(BaseModel):
    default_symbol: str = "XAUUSD"
    default_market_label: str = "Gold"
    default_primary_timeframe: str = "1h"
    default_execution_timeframe: str = "15m"
    preferred_direction: Direction = "either"
    preferred_sessions: list[str] = Field(default_factory=list)
    preferred_source_type: str | None = None
    prefer_zone_from_wick: bool = True
    preferred_zone_mode: ZoneMode = "body_to_wick"
    prefer_retest: bool = True
    prefer_sweep: bool = False
    prefer_fvg: bool = False
    correction_examples: list[str] = Field(default_factory=list)
    learned_aliases: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class StrategyTemplate(BaseModel):
    key: str
    label: str
    description: str
    example_prompt: str
    supported_steps: list[str]


class ChartAnnotation(BaseModel):
    type: str = Field(..., min_length=2, max_length=40)
    label: str = Field(..., min_length=1, max_length=120)
    timeframe: str = "3m"
    price_start: float | None = None
    price_end: float | None = None
    timestamp_start: str | None = None
    timestamp_end: str | None = None
    note: str | None = None

    @field_validator("timeframe")
    @classmethod
    def validate_annotation_timeframe(cls, value: str) -> str:
        if value not in ALLOWED_TFS:
            raise ValueError(f"Unsupported timeframe: {value}")
        return value


class StrategyTemplateRuleSet(BaseModel):
    fresh_only: bool = True
    max_level_touches: int = 1
    reaction_required: bool = False
    reaction_mode: str = "wick_or_displacement"
    fvg_required: bool = False
    poi_mode: str = "fvg_midpoint"
    allow_countertrend: bool = False
    notes: list[str] = Field(default_factory=list)


class AnnotatedExample(BaseModel):
    example_id: str | None = None
    symbol: str = "XAUUSD"
    primary_timeframe: str = "30m"
    execution_timeframe: str = "3m"
    title: str = "Example"
    note: str | None = None
    annotations: list[ChartAnnotation] = Field(default_factory=list)


class StrategyTemplateFile(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: datetime
    updated_at: datetime
    strategy: StrategySpec
    rules: StrategyTemplateRuleSet = Field(default_factory=StrategyTemplateRuleSet)
    examples: list[AnnotatedExample] = Field(default_factory=list)


class StrategyTemplateSaveRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    description: str = ""
    strategy: StrategySpec
    rules: StrategyTemplateRuleSet = Field(default_factory=StrategyTemplateRuleSet)
    examples: list[AnnotatedExample] = Field(default_factory=list)


class StrategyTemplateSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str
    updated_at: str
    school: str
    strategy_key: str
    primary_timeframe: str
    execution_timeframe: str
    examples_count: int = 0


class StrategyTemplateDetail(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str
    updated_at: str
    strategy: StrategySpec
    rules: StrategyTemplateRuleSet = Field(default_factory=StrategyTemplateRuleSet)
    examples: list[AnnotatedExample] = Field(default_factory=list)


class StrategyTemplateExampleSaveRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=120)
    note: str | None = None
    symbol: str = "XAUUSD"
    primary_timeframe: str = "30m"
    execution_timeframe: str = "3m"
    annotations: list[ChartAnnotation] = Field(default_factory=list)


class StrategyTemplateScanRequest(BaseModel):
    symbol: str | None = None
    backtest_range: BacktestRange | None = None
    max_setups: int = 50
    source_lookback: int = 12
    zone_retest_window_bars: int = 72
    ltf_confirmation_window_bars: int = 24
    mss_lookback: int = 6
    outcome_window_bars: int = 36

