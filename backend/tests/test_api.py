from __future__ import annotations

from app.services.market_data import MarketDataError


def test_health_endpoint(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_ready_endpoint(client):
    response = client.get('/ready')
    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_strategy_list_endpoint(client):
    response = client.get('/api/strategy/list')
    assert response.status_code == 200
    assert 'items' in response.json()


def test_invalid_strategy(client):
    response = client.get('/api/strategy/does-not-exist')
    assert response.status_code == 404
    data = response.json()
    assert data['ok'] is False
    assert data['error']['code'] == 'STRATEGY_NOT_FOUND'


def test_missing_market_data_handling(client, monkeypatch):
    from app.main import scanner

    def raise_error(symbol: str, timeframe: str, periods: int = 800):
        raise MarketDataError('boom')

    monkeypatch.setattr(scanner.market, 'fetch', raise_error)
    payload = {'strategy': {'symbol': 'XAUUSD', 'timeframe': '1h', 'lookback_days': 30, 'mode': 'research', 'primary_timeframe': '1h', 'execution_timeframe': '15m', 'direction': 'either', 'sequence': [{'type': 'bos', 'timeframe': '1h', 'direction': 'either', 'params': {}}], 'constraints': {}, 'notes': [], 'unsupported_phrases': [], 'needs_confirmation': False, 'parser_source': 'test', 'confidence': 0.5, 'preferences_applied': []}}
    response = client.post('/api/research/run', json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data['supported'] is False
    assert 'market data' in data['strategy_summary']


def test_system_status_endpoint(client):
    response = client.get('/api/system/status')
    assert response.status_code == 200
    data = response.json()
    assert data['ok'] is True
    assert data['storage']['type'] == 'sqlite'



def test_write_auth_required(client, monkeypatch):
    from app.main import settings
    monkeypatch.setattr(settings, "app_api_key", "secret-key")
    monkeypatch.setattr(settings, "app_require_auth_for_write", True)
    response = client.post('/api/strategy/interpret', json={'text': 'scan gold 1h'})
    assert response.status_code == 400
    assert response.json()['error']['message'] == 'Authentication required'
    ok = client.post('/api/strategy/interpret', headers={'X-API-Key': 'secret-key'}, json={'text': 'scan gold 1h'})
    assert ok.status_code == 200


def test_request_id_header(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.headers.get('X-Request-ID')


def test_capabilities_endpoint(client):
    response = client.get('/api/capabilities')
    assert response.status_code == 200
    data = response.json()
    assert any(item['key'] == 'sr' for item in data['schools'])
    assert any(item['key'] == 'fresh_snr' for item in data['strategies'])


def test_fresh_snr_run_with_mock_data(client, monkeypatch):
    import pandas as pd
    from app.main import scanner

    timestamps = pd.date_range('2026-01-01', periods=80, freq='30min', tz='UTC')
    rows = []
    price = 2600.0
    for idx, ts in enumerate(timestamps):
        if idx == 20:
            open_, high, low, close = 2600.0, 2606.0, 2599.0, 2605.0
        else:
            drift = 0.15 if idx > 20 else 0.05
            open_ = price
            close = price + drift
            high = max(open_, close) + 0.4
            low = min(open_, close) - 0.4
        rows.append({'timestamp': ts, 'open': open_, 'high': high, 'low': low, 'close': close, 'volume': 1000})
        price = close
    df = pd.DataFrame(rows)

    def fake_fetch(symbol: str, timeframe: str, periods: int = 800):
        return df.copy()

    monkeypatch.setattr(scanner.market, 'fetch', fake_fetch)
    monkeypatch.setattr(scanner.market, 'get_last_fetch_report', lambda: {'provider': 'mock', 'cache_hit': False})
    payload = {
        'strategy': {
            'school': 'sr',
            'strategy_key': 'fresh_snr',
            'symbol': 'XAUUSD',
            'timeframe': '30m',
            'lookback_days': 30,
            'mode': 'scan',
            'primary_timeframe': '30m',
            'execution_timeframe': '5m',
            'direction': 'bullish',
            'sequence': [],
            'constraints': {'fresh_only': True, 'strategy_key': 'fresh_snr', 'school': 'sr', 'zone_mode': 'body_to_wick'},
            'notes': [],
            'unsupported_phrases': [],
            'needs_confirmation': False,
            'parser_source': 'test',
            'confidence': 0.9,
            'preferences_applied': [],
        }
    }
    response = client.post('/api/research/run', json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data['supported'] is True
    assert data['total_qualified'] >= 1


def test_capabilities_include_hybrid_strategy(client):
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    data = response.json()
    assert any(item["key"] == "hybrid" for item in data["schools"])
    assert any(item["key"] == "snr_fresh_reaction_fvg_poi" for item in data["strategies"])


def test_composite_snr_fvg_run_with_mock_data(client, monkeypatch):
    import pandas as pd
    from app.main import scanner

    htf_times = pd.date_range("2026-01-01", periods=18, freq="30min", tz="UTC")
    htf_rows = []
    price = 2600.0
    for idx, ts in enumerate(htf_times):
        if idx == 6:
            open_, high, low, close = 2600.2, 2606.0, 2599.4, 2605.4
        else:
            drift = 0.25 if idx > 6 else 0.05
            open_ = price
            close = price + drift
            high = max(open_, close) + 0.35
            low = min(open_, close) - 0.35
        htf_rows.append({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1000})
        price = close
    htf_df = pd.DataFrame(htf_rows)

    ltf_times = pd.date_range("2026-01-01 03:00:00", periods=30, freq="3min", tz="UTC")
    ltf_rows = []
    vals = [
        (2601.2, 2601.4, 2600.7, 2600.9),
        (2600.9, 2601.0, 2600.3, 2600.5),
        (2600.5, 2600.7, 2599.95, 2600.15),
        (2600.15, 2600.35, 2599.8, 2600.28),
        (2600.28, 2600.95, 2600.22, 2600.88),
        (2601.1, 2601.5, 2601.1, 2601.35),
        (2601.45, 2601.95, 2601.42, 2601.82),
        (2601.9, 2602.35, 2601.88, 2602.2),
        (2602.2, 2602.7, 2602.15, 2602.58),
        (2602.58, 2603.0, 2602.5, 2602.86),
    ]
    last = vals[-1]
    while len(vals) < len(ltf_times):
        o = last[3]
        c = o + 0.25
        vals.append((o, c + 0.2, o - 0.1, c))
        last = vals[-1]
    for ts, (open_, high, low, close) in zip(ltf_times, vals):
        ltf_rows.append({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1000})
    ltf_df = pd.DataFrame(ltf_rows)

    def fake_fetch(symbol: str, timeframe: str, periods: int = 800):
        if timeframe == "30m":
            return htf_df.copy()
        if timeframe == "3m":
            return ltf_df.copy()
        raise AssertionError(f"unexpected timeframe {timeframe}")

    monkeypatch.setattr(scanner.market, "fetch", fake_fetch)
    monkeypatch.setattr(scanner.market, "get_last_fetch_report", lambda: {"provider": "mock", "cache_hit": False})

    payload = {
        "strategy": {
            "school": "hybrid",
            "strategy_key": "snr_fresh_reaction_fvg_poi",
            "symbol": "XAUUSD",
            "timeframe": "30m",
            "lookback_days": 30,
            "mode": "research",
            "primary_timeframe": "30m",
            "execution_timeframe": "3m",
            "direction": "bullish",
            "sequence": [
                {"type": "fresh_snr", "timeframe": "30m", "direction": "bullish", "params": {}},
                {"type": "reaction_to_level", "timeframe": "3m", "direction": "bullish", "params": {}},
                {"type": "fvg", "timeframe": "3m", "direction": "bullish", "params": {}},
                {"type": "poi_entry", "timeframe": "3m", "direction": "bullish", "params": {}},
            ],
            "constraints": {"fresh_only": True, "strategy_key": "snr_fresh_reaction_fvg_poi", "school": "hybrid", "zone_mode": "body_to_wick", "poi_mode": "fvg_mid", "confirmation_within_bars": 12},
            "notes": [],
            "unsupported_phrases": [],
            "needs_confirmation": False,
            "parser_source": "test",
            "confidence": 0.9,
            "preferences_applied": [],
        }
    }
    response = client.post("/api/research/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["supported"] is True
    assert data["total_qualified"] >= 1
    assert data["setups"][0]["entry_reference_price"] > 0
    assert any("poi@" in step for step in data["setups"][0]["confirmation_chain"])


def test_strategy_template_save_and_list(client):
    payload = {
        'name': 'Template Fresh SNR',
        'description': 'Named template for repeatable scans',
        'strategy': {
            'school': 'hybrid',
            'strategy_key': 'snr_fresh_reaction_fvg_poi',
            'symbol': 'XAUUSD',
            'timeframe': '30m',
            'lookback_days': 30,
            'mode': 'research',
            'primary_timeframe': '30m',
            'execution_timeframe': '3m',
            'direction': 'bullish',
            'sequence': [
                {'type': 'fresh_snr', 'timeframe': '30m', 'direction': 'bullish', 'params': {}},
                {'type': 'reaction_to_level', 'timeframe': '3m', 'direction': 'bullish', 'params': {}},
                {'type': 'fvg', 'timeframe': '3m', 'direction': 'bullish', 'params': {}},
                {'type': 'poi_entry', 'timeframe': '3m', 'direction': 'bullish', 'params': {}},
            ],
            'constraints': {'poi_mode': 'fvg_midpoint', 'fresh_only': True},
            'notes': [],
            'unsupported_phrases': [],
            'needs_confirmation': False,
            'parser_source': 'test',
            'confidence': 0.91,
            'preferences_applied': [],
        },
        'rules': {
            'fresh_only': True,
            'max_level_touches': 1,
            'reaction_required': True,
            'reaction_mode': 'wick_or_displacement',
            'fvg_required': True,
            'poi_mode': 'fvg_midpoint',
            'allow_countertrend': False,
            'notes': []
        }
    }
    saved = client.post('/api/strategy-templates/save', json=payload)
    assert saved.status_code == 200
    template_id = saved.json()['saved']['id']

    listing = client.get('/api/strategy-templates')
    assert listing.status_code == 200
    assert any(item['id'] == template_id for item in listing.json()['items'])

    detail = client.get(f'/api/strategy-templates/{template_id}')
    assert detail.status_code == 200
    assert detail.json()['strategy']['strategy_key'] == 'snr_fresh_reaction_fvg_poi'


def test_strategy_template_example_and_scan(client, monkeypatch):
    from app.main import scanner
    import pandas as pd

    save_payload = {
        'name': 'Template Scan Fresh SNR',
        'description': 'Scan by stored template',
        'strategy': {
            'school': 'hybrid',
            'strategy_key': 'snr_fresh_reaction_fvg_poi',
            'symbol': 'XAUUSD',
            'timeframe': '30m',
            'lookback_days': 30,
            'mode': 'research',
            'primary_timeframe': '30m',
            'execution_timeframe': '3m',
            'direction': 'bullish',
            'sequence': [
                {'type': 'fresh_snr', 'timeframe': '30m', 'direction': 'bullish', 'params': {}},
                {'type': 'reaction_to_level', 'timeframe': '3m', 'direction': 'bullish', 'params': {}},
                {'type': 'fvg', 'timeframe': '3m', 'direction': 'bullish', 'params': {}},
                {'type': 'poi_entry', 'timeframe': '3m', 'direction': 'bullish', 'params': {}},
            ],
            'constraints': {'poi_mode': 'fvg_midpoint', 'fresh_only': True},
            'notes': [],
            'unsupported_phrases': [],
            'needs_confirmation': False,
            'parser_source': 'test',
            'confidence': 0.91,
            'preferences_applied': [],
        },
        'rules': {
            'fresh_only': True,
            'max_level_touches': 1,
            'reaction_required': True,
            'reaction_mode': 'wick_or_displacement',
            'fvg_required': True,
            'poi_mode': 'fvg_midpoint',
            'allow_countertrend': False,
            'notes': []
        }
    }
    template_id = client.post('/api/strategy-templates/save', json=save_payload).json()['saved']['id']
    example_payload = {
        'title': 'Annotated level example',
        'symbol': 'XAUUSD',
        'primary_timeframe': '30m',
        'execution_timeframe': '3m',
        'annotations': [
            {'type': 'snr', 'label': 'fresh resistance', 'timeframe': '30m', 'price_start': 2350.2},
            {'type': 'fvg', 'label': 'bearish fvg', 'timeframe': '3m', 'price_start': 2349.8, 'price_end': 2349.4},
        ]
    }
    example = client.post(f'/api/strategy-templates/{template_id}/examples', json=example_payload)
    assert example.status_code == 200
    assert len(example.json()['saved']['examples']) >= 1

    htf_times = pd.date_range('2026-01-01', periods=18, freq='30min', tz='UTC')
    htf_rows, price = [], 2600.0
    for idx, ts in enumerate(htf_times):
        if idx == 6:
            open_, high, low, close = 2600.2, 2606.0, 2599.4, 2605.4
        else:
            drift = 0.25 if idx > 6 else 0.05
            open_ = price
            close = price + drift
            high = max(open_, close) + 0.35
            low = min(open_, close) - 0.35
        htf_rows.append({'timestamp': ts, 'open': open_, 'high': high, 'low': low, 'close': close, 'volume': 1000})
        price = close
    htf_df = pd.DataFrame(htf_rows)

    ltf_times = pd.date_range('2026-01-01 03:00:00', periods=30, freq='3min', tz='UTC')
    vals = [
        (2601.2, 2601.4, 2600.7, 2600.9),
        (2600.9, 2601.0, 2600.3, 2600.5),
        (2600.5, 2600.7, 2599.95, 2600.15),
        (2600.15, 2600.35, 2599.8, 2600.28),
        (2600.28, 2600.95, 2600.22, 2600.88),
        (2601.1, 2601.5, 2601.1, 2601.35),
        (2601.45, 2601.95, 2601.42, 2601.82),
        (2601.9, 2602.35, 2601.88, 2602.2),
        (2602.2, 2602.7, 2602.15, 2602.58),
        (2602.58, 2603.0, 2602.5, 2602.86),
    ]
    last = vals[-1]
    while len(vals) < len(ltf_times):
        o = last[3]
        c = o + 0.25
        vals.append((o, c + 0.2, o - 0.1, c))
        last = vals[-1]
    ltf_df = pd.DataFrame([
        {'timestamp': ts, 'open': open_, 'high': high, 'low': low, 'close': close, 'volume': 1000}
        for ts, (open_, high, low, close) in zip(ltf_times, vals)
    ])

    def fake_fetch(symbol: str, timeframe: str, periods: int = 800):
        if timeframe == '30m':
            return htf_df.copy()
        if timeframe == '3m':
            return ltf_df.copy()
        raise AssertionError(f'unexpected timeframe {timeframe}')

    monkeypatch.setattr(scanner.market, 'fetch', fake_fetch)
    monkeypatch.setattr(scanner.market, 'get_last_fetch_report', lambda: {'provider': 'mock', 'cache_hit': False})
    scan = client.post(f'/api/strategy-templates/{template_id}/scan', json={'backtest_range': {'start': '2026-01-01', 'end': '2026-01-05'}})
    assert scan.status_code == 200
    data = scan.json()
    assert data['supported'] is True
    assert data['run_id']
    assert data['highlights'][0].startswith('Template:')
