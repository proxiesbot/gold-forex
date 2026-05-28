from __future__ import annotations

from app.repositories.preference_store import PreferenceStore
from app.services.llm_parser import StrategyInterpreter


async def _parse(text: str):
    interpreter = StrategyInterpreter(PreferenceStore())
    return await interpreter.interpret(text)


def test_parser_arabic_command():
    import asyncio
    result = asyncio.run(_parse('حلل الذهب اليوم'))
    assert result.understood.symbol == 'XAUUSD'
    assert result.understood.mode == 'research'
    assert result.understood.lookback_days == 1


def test_parser_english_command():
    import asyncio
    result = asyncio.run(_parse('scan gold 1h last 90 days'))
    assert result.understood.symbol == 'XAUUSD'
    assert result.understood.mode == 'scan'
    assert result.understood.primary_timeframe == '1h'
    assert result.understood.lookback_days == 90

def test_parser_fresh_snr_command():
    import asyncio
    result = asyncio.run(_parse('حددلي SNR فريش على فريم 30 دقيقة'))
    assert result.understood.school == 'sr'
    assert result.understood.strategy_key == 'fresh_snr'
    assert result.understood.primary_timeframe == '30m'


def test_parser_fresh_supply_command():
    import asyncio
    result = asyncio.run(_parse('اختبر استراتيجية fresh supply على الذهب 4h'))
    assert result.understood.school == 'supply_demand'
    assert result.understood.strategy_key == 'fresh_supply'
    assert result.understood.direction == 'bearish'


def test_parser_composite_snr_fvg_poi_command():
    import asyncio
    text_cmd = "استراتيجيتي هي تحديد SNR على فريم 30 دقيقة في حال كان فريش وبعدها ينزل على فريم 3 دقايق لازم يكون السعر تفاعل معها وعمل FVG على فريم 3 وبعدها أنا بدخل من نقطة الاهتمام"
    result = asyncio.run(_parse(text_cmd))
    assert result.understood.school == "hybrid"
    assert result.understood.strategy_key == "snr_fresh_reaction_fvg_poi"
    assert result.understood.primary_timeframe == "30m"
    assert result.understood.execution_timeframe == "3m"
    assert result.understood.constraints["poi_mode"] == "fvg_mid"
