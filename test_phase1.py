"""Quick test script for Phase 1 verification"""
import asyncio
import sys
sys.path.insert(0, '.')

async def test_rest():
    from data_engine.binance_client import BinanceClient
    client = BinanceClient()
    candles = await client.fetch_historical_klines('BTCUSDT', '15m', limit=5)
    if candles:
        print('=== REST API Test: OK ===')
        for c in candles[-3:]:
            dt = c["datetime"].strftime("%Y-%m-%d %H:%M")
            print(f'  {dt} | O={c["open"]:.2f} H={c["high"]:.2f} L={c["low"]:.2f} C={c["close"]:.2f}')
    else:
        print('ERROR: Cannot fetch data')
    return len(candles) > 0

async def test_datastore():
    from data_engine.binance_client import BinanceClient
    from data_engine.data_store import DataStore
    client = BinanceClient()
    store = DataStore()
    await store.initialize_from_history(client)
    report = store.status_report()
    print('\n=== DataStore Status ===')
    for key, info in report.items():
        ready = "READY" if info["ready"] else "NOT READY"
        print(f'  {key}: {info["size"]} candles | {ready} | Last Close={info["latest_close"]}')
    return True

async def test_db():
    from database.db_manager import DatabaseManager
    db = DatabaseManager()
    await db.init()
    await db.log_event("INFO", "TEST", "Phase 1 test successful")
    stats = await db.get_stats()
    print(f'\n=== Database Test: OK ===')
    print(f'  Stats: {stats}')
    await db.close()
    return True

async def main():
    print('SENTRY_TRADER Phase 1 — Test Suite')
    print('=' * 50)
    
    r1 = await test_rest()
    r2 = await test_datastore()
    r3 = await test_db()
    
    print('\n' + '=' * 50)
    if all([r1, r2, r3]):
        print('ALL TESTS PASSED — Phase 1 ready!')
    else:
        print('SOME TESTS FAILED')

asyncio.run(main())
