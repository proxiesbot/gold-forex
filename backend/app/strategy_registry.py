from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import StrategyNode, StrategyTemplate


@dataclass(frozen=True)
class StrategyDefinition:
    key: str
    school: str
    label: str
    description: str
    aliases: tuple[str, ...]
    supported_modes: tuple[str, ...] = ("research", "scan", "backtest")
    default_sequence: tuple[dict[str, Any], ...] = ()
    default_direction: str = "either"
    tags: tuple[str, ...] = ()

    def build_sequence(self, primary_tf: str, execution_tf: str, direction: str) -> list[StrategyNode]:
        resolved_direction = direction if direction in {"bullish", "bearish"} else self.default_direction
        return [
            StrategyNode(
                type=item["type"],
                timeframe=item.get("timeframe") or (primary_tf if idx == 0 else execution_tf),
                direction=item.get("direction") or resolved_direction,
                params=dict(item.get("params") or {}),
            )
            for idx, item in enumerate(self.default_sequence)
        ]


@dataclass(frozen=True)
class SchoolDefinition:
    key: str
    label: str
    description: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


SCHOOLS: dict[str, SchoolDefinition] = {
    "smc": SchoolDefinition(
        key="smc",
        label="SMC / ICT",
        description="Structure, liquidity, imbalance, breakers, and confirmation chains.",
        aliases=("smc", "ict", "smart money", "اسمارت موني", "سمارت موني"),
    ),
    "sr": SchoolDefinition(
        key="sr",
        label="Support & Resistance",
        description="Fresh levels, flips, touch-count logic, and classic support/resistance.",
        aliases=("snr", "s&r", "sr", "support resistance", "دعم", "مقاومة", "دعم ومقاومة"),
    ),
    "supply_demand": SchoolDefinition(
        key="supply_demand",
        label="Supply & Demand",
        description="Fresh supply and demand zones with retest-aware zone construction.",
        aliases=("supply", "demand", "supply demand", "عرض وطلب", "سبلاي", "ديماند"),
    ),
    "hybrid": SchoolDefinition(
        key="hybrid",
        label="Hybrid multi-timeframe",
        description="Blend higher-timeframe levels with lower-timeframe confirmation and execution logic.",
        aliases=("hybrid", "multi timeframe", "متعدد الفريمات", "mtf", "poi"),
    ),
}


STRATEGIES: dict[str, StrategyDefinition] = {
    "smc_core": StrategyDefinition(
        key="smc_core",
        school="smc",
        label="SMC core flow",
        description="Classic source -> retest -> sweep -> MSS -> FVG chain.",
        aliases=("smc", "ict", "smart money", "bos", "mss", "liquidity sweep", "sweep", "fvg", "breaker"),
        default_sequence=(
            {"type": "bos"},
            {"type": "retest_zone"},
            {"type": "liquidity_sweep"},
            {"type": "mss"},
            {"type": "fvg"},
        ),
    ),
    "fresh_snr": StrategyDefinition(
        key="fresh_snr",
        school="sr",
        label="Fresh S&R",
        description="Detect fresh support/resistance levels that remain untouched after creation.",
        aliases=("fresh snr", "fresh sr", "support resistance", "snr", "s&r", "sr", "fresh support", "fresh resistance", "دعم مقاومة", "snr فريش", "فريش snr", "حددلي snr", "حددلي s&r"),
        default_sequence=(),
    ),
    "level_flip": StrategyDefinition(
        key="level_flip",
        school="sr",
        label="Support/Resistance flip",
        description="Identify levels that flipped from support to resistance or vice versa.",
        aliases=("flip level", "sr flip", "support resistance flip", "flip"),
        default_sequence=(),
    ),
    "fresh_supply": StrategyDefinition(
        key="fresh_supply",
        school="supply_demand",
        label="Fresh supply zone",
        description="Detect fresh bearish supply zones that have not been mitigated yet.",
        aliases=("fresh supply", "supply zone", "عرض", "سبلاي", "fresh supply zone"),
        default_sequence=(),
        default_direction="bearish",
    ),
    "fresh_demand": StrategyDefinition(
        key="fresh_demand",
        school="supply_demand",
        label="Fresh demand zone",
        description="Detect fresh bullish demand zones that have not been mitigated yet.",
        aliases=("fresh demand", "demand zone", "طلب", "ديماند", "fresh demand zone"),
        default_sequence=(),
        default_direction="bullish",
    ),
    "snr_fresh_reaction_fvg_poi": StrategyDefinition(
        key="snr_fresh_reaction_fvg_poi",
        school="hybrid",
        label="Fresh S&R + reaction + FVG + POI",
        description="Find fresh 30m S&R, wait for lower-timeframe reaction, confirm with FVG, and derive a POI entry.",
        aliases=(
            "fresh snr reaction fvg poi",
            "snr reaction fvg",
            "fresh snr fvg",
            "poi entry",
            "نقطة الاهتمام",
            "تفاعل",
            "حددلي snr",
            "عمل fvg",
            "فريم 3",
            "فريش",
        ),
        default_sequence=(
            {"type": "fresh_snr"},
            {"type": "reaction_to_level"},
            {"type": "fvg"},
            {"type": "poi_entry"},
        ),
    ),
}


def all_schools() -> list[dict[str, str]]:
    return [
        {"key": item.key, "label": item.label, "description": item.description}
        for item in SCHOOLS.values()
    ]


def all_strategies() -> list[dict[str, Any]]:
    return [
        {
            "key": item.key,
            "school": item.school,
            "label": item.label,
            "description": item.description,
            "aliases": list(item.aliases),
            "supported_modes": list(item.supported_modes),
            "tags": list(item.tags),
        }
        for item in STRATEGIES.values()
    ]


def detect_school(text: str) -> SchoolDefinition | None:
    lowered = text.lower()
    for school in SCHOOLS.values():
        if any(alias in lowered for alias in school.aliases):
            return school
    return None


def detect_strategy(text: str) -> StrategyDefinition | None:
    lowered = text.lower()
    ranked: list[tuple[int, StrategyDefinition]] = []
    for definition in STRATEGIES.values():
        hits = sum(1 for alias in definition.aliases if alias in lowered)
        if hits:
            ranked.append((hits, definition))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], len(item[1].aliases)), reverse=True)
    return ranked[0][1]


def strategy_templates() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for definition in STRATEGIES.values():
        if definition.default_sequence:
            templates.append(
                StrategyTemplate(
                    key=definition.key,
                    label=definition.label,
                    description=definition.description,
                    example_prompt=f"Use {definition.label} on gold 1h",
                    supported_steps=[item["type"] for item in definition.default_sequence],
                ).model_dump()
            )
    return templates
