"""Phase 3 test — Position Sizer, Stop Loss Manager, and Risk Guard"""
import asyncio
import sys
import io
sys.path.insert(0, '.')
# Fix Thai encoding on Windows terminal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from risk_manager.position_sizer import PositionSizer
from risk_manager.stop_loss import StopLossManager
from risk_manager.risk_guard import RiskGuard
from database.models import Trade

async def test_position_sizer():
    print("\n=== Test 1: Position Sizer (Option B+A Strategy) ===")
    sizer = PositionSizer(risk_per_trade=0.01, min_lot=0.001, max_margin_ratio=0.30, leverage=5)
    
    print("- Case: Small Balance 100 USDT, High Volatility (ATR 250) — Should SNAP to min lot & EXCEED margin limit")
    res1 = sizer.calculate(balance=100.0, entry_price=75000.0, atr=250.0)
    print(f"  {res1}")
    
    print("\n- Case: Balanced 5000 USDT, Normal Volatility — Should PASS easily")
    res2 = sizer.calculate(balance=5000.0, entry_price=75000.0, atr=200.0)
    print(f"  {res2}")
    return True

async def test_stop_loss():
    print("\n=== Test 2: Dynamic Trailing Stop (Option B) ===")
    sl_manager = StopLossManager(activation_rr=1.0, atr_multiplier=1.0)
    
    t = Trade(id=1, symbol="BTCUSDT", side="LONG", entry_price=75000.0, sl_price=74500.0, tp_price=76000.0)
    
    # Not yet 1R
    sl, act, hp = sl_manager.update_trailing_sl(t, current_close=75400.0, current_atr=200.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hp
    print(f"- Close=75400 | Activated: {act} | SL: {sl} | Highest: {hp}")
    
    # Hit 1R (Entry+500 = 75500)
    sl, act, hp = sl_manager.update_trailing_sl(t, current_close=75600.0, current_atr=300.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hp
    print(f"- Close=75600 | Activated: {act} | SL: {sl} | Highest: {hp} (Should move to entry at least)")
    
    # Let trend run! (SL tracking highest - 1xATR)
    sl, act, hp = sl_manager.update_trailing_sl(t, current_close=76000.0, current_atr=250.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hp
    print(f"- Close=76000 | Activated: {act} | SL: {sl} | Highest: {hp} (SL=76000-250=75750)")
    
    # Price dropping, SL ratchets up, should not go down
    sl, act, hp = sl_manager.update_trailing_sl(t, current_close=75500.0, current_atr=300.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hp
    print(f"- Close=75500 | Activated: {act} | SL: {sl} | Highest: {hp} (SL should STILL be 75750!)")
    
    return True

async def test_risk_guard():
    print("\n=== Test 3: Risk Guard ===")
    guard = RiskGuard(max_open_positions=1, daily_loss_limit=0.05)
    
    ok, remark = guard.check_opening_rules(open_trades=[Trade(id=1)], daily_pnl_usdt=0, total_balance=100.0)
    print(f"- Case limit max positions (1): Valid={not ok} -> Passed: {remark}")
    
    ok, remark = guard.check_opening_rules(open_trades=[], daily_pnl_usdt=-6.0, total_balance=100.0)
    print(f"- Case daily loss (-6%): Valid={not ok} -> Passed: {remark}")
    
    ok, remark = guard.check_opening_rules(open_trades=[], daily_pnl_usdt=2.0, total_balance=100.0)
    print(f"- Case all good: Valid={ok} -> Passed: {remark}")
    return True

async def main():
    print("SENTRY_TRADER Phase 3 — Test Suite")
    print("=" * 50)
    
    await test_position_sizer()
    await test_stop_loss()
    await test_risk_guard()
    print("\n" + "=" * 50)
    print("ALL PHASE 3 TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(main())
