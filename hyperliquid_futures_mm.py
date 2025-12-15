import asyncio
from datetime import datetime
import time
import requests
import numpy as np
import hyperliquid_trade

API_URL = 'https://api.hyperliquid.xyz/info'

COIN = 'BTC'
SIZE_DECIMALS = 3
TICK_SIZE = 1

ORDER_SIZE_USD = 500
CHECK_INTERVAL = 60
MAX_OPEN_ORDERS = 50
ORDER_EXPIRY_MINUTES = 15

# spread settings
LONG_SPREADS = [0.001, 0.002, 0.003, 0.004, 0.005]
SHORT_SPREADS = [0.001, 0.002, 0.003, 0.004, 0.005]
ORDER_RATIOS = [0.50, 0.20, 0.10, 0.10, 0.10]

# Position limits
MAX_POSITION_USD = 10000
INVENTORY_SKEW_MULTIPLIER = 0.25

# ATR settings
ATR_INTERVAL = '5m'
ATR_PERIOD = 14
BASE_SPREAD = 0.001
VOL_MULTIPLIER_MIN = 0.5
VOL_MULTIPLIER_MAX = 2.0


class HyperliquidFuturesMM:
    def __init__(self):
        try:
            self.trader = hyperliquid_trade.HyperliquidTrader(
                coin=COIN,
                symbol=COIN,
                tick_size=TICK_SIZE,
                size_decimals=SIZE_DECIMALS
            )
            self.trading_enabled = True
        except Exception as e:
            print(f"[ERROR] Trading init failed: {e}")
            self.trading_enabled = False

    @staticmethod
    def format_quantity(qty):
        return int(round(qty)) if SIZE_DECIMALS == 0 else round(qty, SIZE_DECIMALS)

    @staticmethod
    def format_price(price):
        return round(price / TICK_SIZE) * TICK_SIZE

    def get_candles(self, interval='5m', limit=20):
        interval_ms = {'1m': 60_000, '5m': 300_000, '15m': 900_000, '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000}
        end_time = int(time.time() * 1000)
        start_time = end_time - (interval_ms[interval] * limit)

        payload = {
            'type': 'candleSnapshot',
            'req': {'coin': COIN, 'interval': interval, 'startTime': start_time, 'endTime': end_time}
        }
        try:
            response = requests.post(API_URL, json=payload, timeout=10)
            return response.json() if response.status_code == 200 else []
        except:
            return []

    def calculate_atr(self, candles, period=14):
        if len(candles) < period + 1:
            return None

        tr_list = []
        for i in range(1, len(candles)):
            high, low = float(candles[i]['h']), float(candles[i]['l'])
            prev_close = float(candles[i-1]['c'])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        atr = np.mean(tr_list[:period])
        for tr in tr_list[period:]:
            atr = (tr / period) + (atr * (1 - 1/period))
        return atr

    def get_volatility_multiplier(self, mid_price):
        candles = self.get_candles(ATR_INTERVAL, ATR_PERIOD + 5)
        if not candles:
            return 1.0
        atr = self.calculate_atr(candles, ATR_PERIOD)
        if atr is None or mid_price <= 0:
            return 1.0
        vol_mult = (atr / mid_price) / BASE_SPREAD
        return max(VOL_MULTIPLIER_MIN, min(VOL_MULTIPLIER_MAX, vol_mult))

    def calculate_inventory_adjusted_spreads(self, pos_ratio, vol_mult=1.0):
        adj = pos_ratio * INVENTORY_SKEW_MULTIPLIER
        long_spreads = [max(0.0001, s * (1 + adj) * vol_mult) for s in LONG_SPREADS]
        short_spreads = [max(0.0001, s * (1 - adj) * vol_mult) for s in SHORT_SPREADS]
        return long_spreads, short_spreads

    async def _place_orders_sequential(self, order_method, orders, delay=0.2):
        results = []
        for i, (qty, price) in enumerate(orders, 1):
            result = await asyncio.to_thread(order_method, qty, price)
            results.append(result)
            if i < len(orders):
                await asyncio.sleep(delay)
        return results

    async def get_mid_price(self):
        try:
            return await asyncio.to_thread(self.trader.get_mid_price)
        except Exception as e:
            print(f"[ERROR] get_mid_price: {e}")
            return None

    async def get_position(self):
        try:
            return await asyncio.to_thread(self.trader.get_perp_position)
        except Exception as e:
            print(f"[ERROR] get_position: {e}")
            return {'size': 0, 'entry_price': 0, 'unrealized_pnl': 0, 'margin_used': 0}

    async def place_orders(self, side, mid, pos_val, pos_ratio, vol_mult, open_cnt):
        if not self.trading_enabled:
            return False

        is_long = (side == 'long')
        side_name = 'LONG' if is_long else 'SHORT'
        num_tiers = len(ORDER_RATIOS)

        # Check slots
        if open_cnt + num_tiers > MAX_OPEN_ORDERS:
            print(f"[ORDER] Skip {side_name} (need {num_tiers} slots, only {MAX_OPEN_ORDERS - open_cnt} available)")
            return False

        # Check position limits
        if is_long and pos_val >= MAX_POSITION_USD:
            print(f"[ORDER] Skip {side_name} (pos_ratio {pos_ratio:.1%} >= 100%)")
            return False
        if not is_long and pos_val <= -MAX_POSITION_USD:
            print(f"[ORDER] Skip {side_name} (pos_ratio {pos_ratio:.1%} <= -100%)")
            return False

        # Calculate spreads
        long_sp, short_sp = self.calculate_inventory_adjusted_spreads(pos_ratio, vol_mult)
        spreads = long_sp if is_long else short_sp

        # Build orders
        orders = []
        for ratio, spread in zip(ORDER_RATIOS, spreads):
            price = self.format_price(mid * (1 - spread if is_long else 1 + spread))
            qty = self.format_quantity((ORDER_SIZE_USD * ratio) / price)
            orders.append((qty, price))

        # Execute
        try:
            order_method = self.trader.perp_long if is_long else self.trader.perp_short
            results = await self._place_orders_sequential(order_method, orders)
            success_cnt = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))

            sign = '-' if is_long else '+'
            order_str = "  ".join([f"{sign}{s*100:.2f}% @{int(p):,}" for (q, p), s in zip(orders, spreads)])
            print(f"[ORDER] {side_name}({success_cnt}/{num_tiers}): {order_str}")
            return success_cnt > 0
        except Exception as e:
            print(f"[ERROR] {side_name} orders: {e}")
            return False

    async def cancel_old_orders(self):
        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            now = time.time()
            cancelled_long = cancelled_short = 0

            for order in open_orders:
                age_min = (now * 1000 - order['timestamp']) / 1000 / 60
                if age_min > ORDER_EXPIRY_MINUTES:
                    result = await asyncio.to_thread(self.trader.cancel_order, order['oid'])
                    if result.get('success'):
                        if order['side'] == 'buy':
                            cancelled_long += 1
                        else:
                            cancelled_short += 1

            if cancelled_long > 0 or cancelled_short > 0:
                print(f"[CANCEL] {cancelled_long} long, {cancelled_short} short (>{ORDER_EXPIRY_MINUTES}min)")
        except Exception as e:
            print(f"[ERROR] cancel_old: {e}")

    async def run_single_iteration(self):
        try:
            mid = await self.get_mid_price()
            if not mid:
                return

            vol_mult = self.get_volatility_multiplier(mid)
            position = await self.get_position()
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)

            pos_val = position['size'] * mid
            pos_ratio = pos_val / MAX_POSITION_USD if MAX_POSITION_USD > 0 else 0
            inv_adj = pos_ratio * INVENTORY_SKEW_MULTIPLIER

            pos_status = "Neutral" if abs(position['size']) < 0.001 else ("Long" if position['size'] > 0 else "Short")

            # HIP-style log
            print(f"\n{'='*50}")
            print(f"[{datetime.now().strftime('%H:%M:%S')}]")
            print(f"{COIN} | Mid: {mid:,.0f} | Vol: {vol_mult:.2f}x | Inv: {inv_adj:+.2f} ({pos_ratio:+.1%})")
            print(f"Pos: {position['size']:.3f} {COIN} (${abs(pos_val):,.0f}) {pos_status} | Entry: {position['entry_price']:,.0f} | PnL: {position['unrealized_pnl']:+.2f}")

            long_cnt = len([o for o in open_orders if o['side'] == 'buy'])
            short_cnt = len([o for o in open_orders if o['side'] == 'sell'])

            await self.place_orders('long', mid, pos_val, pos_ratio, vol_mult, long_cnt)
            await self.place_orders('short', mid, pos_val, pos_ratio, vol_mult, short_cnt)

            print(f"[ORDERS] {long_cnt} buys, {short_cnt} sells")
            await self.cancel_old_orders()

        except Exception as e:
            print(f"[ERROR] iteration: {e}")

    async def run(self):
        print(f"[START] {COIN} | Size: {ORDER_SIZE_USD:,} | Max: {MAX_POSITION_USD:,}")
        print(f"[CONFIG] Spreads: {LONG_SPREADS} | Ratios: {ORDER_RATIOS}")
        print(f"[CONFIG] Skew: {INVENTORY_SKEW_MULTIPLIER}x | ATR: {ATR_INTERVAL}/{ATR_PERIOD}")
        print("=" * 50)

        while True:
            try:
                await self.run_single_iteration()
                await asyncio.sleep(CHECK_INTERVAL)
            except KeyboardInterrupt:
                print("\n[STOP] Shutting down...")
                break
            except Exception as e:
                print(f"[ERROR] main: {e}")
                await asyncio.sleep(5)


async def main():
    mm = HyperliquidFuturesMM()
    await mm.run()


if __name__ == "__main__":
    asyncio.run(main())
