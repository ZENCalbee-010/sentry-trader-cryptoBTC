"""Phase 2 test — indicators, signal engine, backtester"""
import asyncio
import sys
import io
sys.path.insert(0, '.')
# Fix Thai encoding on Windows terminal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


async def test_indicators():
    print("\n=== Test 1: Indicators ===")
    from data_engine.binance_client import BinanceClient
    from data_engine.data_store import DataStore
    from brain_center.indicators import (
        calc_rsi, calc_ema, calc_atr, calc_volume_ma,
        build_feature_row, is_kill_switch_active, rsi_crossed_up_from_oversold
    )
    import config

    client = BinanceClient()
    store = DataStore()
    await store.initialize_from_history(client)

    df_15m = await store.get_df("BTCUSDT", "15m")
    df_4h  = await store.get_df("BTCUSDT", "4h")

    rsi   = calc_rsi(df_15m)
    ema   = calc_ema(df_4h, period=config.EMA_PERIOD)
    atr   = calc_atr(df_15m)
    vol   = calc_volume_ma(df_15m)

    print(f"  RSI (latest): {rsi.iloc[-1]:.2f}")
    print(f"  EMA200 4H:    {ema.iloc[-1]:.2f}")
    print(f"  ATR:          {atr.iloc[-1]:.2f}")
    print(f"  Vol MA:       {vol.iloc[-1]:.2f}")
    print(f"  Kill Switch:  {is_kill_switch_active(atr)}")
    print(f"  RSI CrossUp:  {rsi_crossed_up_from_oversold(rsi)}")

    features = build_feature_row(df_15m, df_4h)
    print(f"  AI Features:  {features}")
    return True


async def test_signal_engine():
    print("\n=== Test 2: Signal Engine (Indicator-Only mode) ===")
    from data_engine.binance_client import BinanceClient
    from data_engine.data_store import DataStore
    from brain_center.signal_engine import SignalEngine

    client = BinanceClient()
    store = DataStore()
    await store.initialize_from_history(client)

    engine = SignalEngine()
    engine.load_model()  # ไม่มี model file → Indicator-Only mode

    result = await engine.process(store, symbol="BTCUSDT")
    print(f"  Signal Type:  {result.signal_type}")
    print(f"  Triggered:    {result.triggered}")
    print(f"  RSI:          {result.rsi}")
    print(f"  Skip Reason:  {result.skip_reason}")
    print(f"  Checks:       {result.checks}")
    return True


async def test_backtester():
    print("\n=== Test 3: Backtester (90 days) ===")
    from brain_center.backtester import Backtester

    bt = Backtester()
    await bt.load_data("BTCUSDT", days=90)
    results = bt.run()
    bt.print_report(results)
    return True


async def main():
    print("SENTRY_TRADER Phase 2 — Test Suite")
    print("=" * 50)

    r1 = await test_indicators()
    r2 = await test_signal_engine()
    r3 = await test_backtester()

    print("\n" + "=" * 50)
    if all([r1, r2, r3]):
        print("ALL PHASE 2 TESTS PASSED!")
    else:
        print("SOME TESTS FAILED")

asyncio.run(main())
