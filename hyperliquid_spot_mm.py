import asyncio
from datetime import datetime
import time

import requests
import numpy as np
import hyperliquid_trade

API_URL = 'https://api.hyperliquid.xyz/info'

COIN = 'BTC'
SPOT_SYMBOL = '@142'
SIZE_DECIMALS = 5
TICK_SIZE = 1

ORDER_SIZE_USD = 500
CHECK_INTERVAL = 60
MAX_OPEN_ORDERS = 30
ORDER_EXPIRY_MINUTES = 15

# Spreads and ratios for 5-tier orders
BUY_SPREADS = [0.001, 0.002, 0.003, 0.004, 0.005]
SELL_SPREADS = [0.001, 0.002, 0.003, 0.004, 0.005]
ORDER_RATIOS = [0.50, 0.20, 0.10, 0.10, 0.10]

# Inventory limits
MIN_SELL_RATIO = 0.1
MAX_COIN_RATIO = 0.7
TARGET_COIN_RATIO = 0.5
INVENTORY_SKEW_MULTIPLIER = 1

# ATR settings
ATR_INTERVAL = '5m'
ATR_PERIOD = 14
BASE_SPREAD = 0.001
VOL_MULTIPLIER_MIN = 0.5
VOL_MULTIPLIER_MAX = 3.0


class MarketMaker:
    def __init__(self):
        try:
            self.trader = hyperliquid_trade.HyperliquidTrader(
                coin=COIN,
                symbol=SPOT_SYMBOL,
                tick_size=TICK_SIZE,
                size_decimals=SIZE_DECIMALS
            )
            self.trading_enabled = True
        except Exception as e:
            print(f"Trading initialization failed: {e}")
            self.trading_enabled = False

    @staticmethod
    def format_quantity(quantity):
        return int(round(quantity)) if SIZE_DECIMALS == 0 else round(quantity, SIZE_DECIMALS)

    @staticmethod
    def format_price(price):
        return round(price / TICK_SIZE) * TICK_SIZE

    def get_candles(self, interval: str = '5m', limit: int = 20):
        """Fetch candles from Hyperliquid API"""
        interval_ms = {
            '1m': 60_000, '5m': 300_000, '15m': 900_000,
            '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000
        }
        end_time = int(time.time() * 1000)
        start_time = end_time - (interval_ms[interval] * limit)

        payload = {
            'type': 'candleSnapshot',
            'req': {
                'coin': COIN,
                'interval': interval,
                'startTime': start_time,
                'endTime': end_time
            }
        }
        response = requests.post(API_URL, json=payload)
        return response.json() if response.status_code == 200 else []

    def calculate_atr(self, candles, period: int = 14):
        """Calculate ATR using Wilder's EMA method"""
        if len(candles) < period + 1:
            return None

        tr_list = []
        for i in range(1, len(candles)):
            high = float(candles[i]['h'])
            low = float(candles[i]['l'])
            prev_close = float(candles[i-1]['c'])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        atr = np.mean(tr_list[:period])
        multiplier = 1 / period
        for tr in tr_list[period:]:
            atr = (tr * multiplier) + (atr * (1 - multiplier))
        return atr

    def get_volatility_multiplier(self, mid_price):
        """Calculate volatility multiplier based on ATR"""
        candles = self.get_candles(ATR_INTERVAL, ATR_PERIOD + 5)
        if not candles:
            return 1.0

        atr = self.calculate_atr(candles, ATR_PERIOD)
        if atr is None or mid_price <= 0:
            return 1.0

        atr_ratio = atr / mid_price
        vol_multiplier = max(VOL_MULTIPLIER_MIN, min(VOL_MULTIPLIER_MAX, atr_ratio / BASE_SPREAD))
        return vol_multiplier

    def calculate_inventory_adjusted_spreads(self, coin_ratio, vol_multiplier=1.0):
        """Adjust spreads based on inventory imbalance and volatility"""
        adj = (coin_ratio - TARGET_COIN_RATIO) * INVENTORY_SKEW_MULTIPLIER
        buy_spreads = [max(0.0001, s * (1 + adj) * vol_multiplier) for s in BUY_SPREADS]
        sell_spreads = [max(0.0001, s * (1 - adj) * vol_multiplier) for s in SELL_SPREADS]
        return buy_spreads, sell_spreads

    async def _place_orders_sequential(self, order_method, orders, delay=0.2):
        """Helper to place orders sequentially with delay"""
        results = []
        for i, (qty, price) in enumerate(orders, 1):
            result = await asyncio.to_thread(order_method, qty, price)
            results.append(result)
            if i < len(orders):
                await asyncio.sleep(delay)
        return results

    async def get_mid_price(self):
        """Get mid price via allMids API"""
        try:
            return await asyncio.to_thread(self.trader.get_mid_price)
        except Exception as e:
            print(f"Mid price error: {e}")
            return None

    async def get_balance(self):
        """Get BTC and USDC balance"""
        try:
            balances = await asyncio.to_thread(self.trader.get_spot_balance)
            coin_balance = float(balances.get('UBTC', 0))
            usdc_balance = float(balances.get('USDC', 0))
            return coin_balance, usdc_balance
        except Exception as e:
            print(f"Balance error: {e}")
            return 0, 0

    async def place_orders(self, side, mid_price, coin_ratio, coin_balance, usdc_balance, vol_multiplier, open_orders_count):
        """Place 5-tier orders (unified for buy/sell) with inventory-adjusted spreads"""
        if not self.trading_enabled:
            return False

        is_buy = (side == 'buy')
        side_name = 'BUY' if is_buy else 'SELL'

        # Check max open orders
        if open_orders_count >= MAX_OPEN_ORDERS:
            print(f"  Max open {side_name} orders reached ({open_orders_count}/{MAX_OPEN_ORDERS}), skipping")
            return False

        # Check inventory limits
        if is_buy:
            if coin_ratio >= MAX_COIN_RATIO:
                print(f"  Skip {side_name} (coin ratio {coin_ratio:.1%} >= {MAX_COIN_RATIO:.1%})")
                return False
            if usdc_balance < ORDER_SIZE_USD:
                print(f"  Skip {side_name} (USDC {usdc_balance:.2f} < {ORDER_SIZE_USD})")
                return False
        else:
            if coin_balance < 0.0001:
                print(f"  Skip {side_name} (BTC balance {coin_balance:.6f} too low)")
                return False

        # Calculate spreads
        buy_spreads, sell_spreads = self.calculate_inventory_adjusted_spreads(coin_ratio, vol_multiplier)
        spreads = buy_spreads if is_buy else sell_spreads

        # Build orders
        orders = []
        for ratio, spread in zip(ORDER_RATIOS, spreads):
            usd_amount = ORDER_SIZE_USD * ratio
            price = self.format_price(mid_price * (1 - spread if is_buy else 1 + spread))
            qty = self.format_quantity(usd_amount / price)
            orders.append((qty, price))

        # Check sell quantity
        if not is_buy:
            total_sell_qty = sum(qty for qty, _ in orders)
            if total_sell_qty > coin_balance:
                print(f"  Insufficient BTC (need {total_sell_qty:.6f}, have {coin_balance:.6f}), skipping")
                return False

        # Execute orders
        try:
            order_method = self.trader.spot_buy if is_buy else self.trader.spot_sell
            results = await self._place_orders_sequential(order_method, orders)
            success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))

            sign = '-' if is_buy else '+'
            order_str = "  ".join([f"{sign}{spread*100:.2f}% @{int(price)}" for (qty, price), spread in zip(orders, spreads)])
            print(f"{side_name}({success_count}/5): {order_str}")

            return success_count > 0

        except Exception as e:
            print(f"{side_name} orders error: {e}")
            return False

    async def cancel_old_orders(self):
        """Cancel orders older than ORDER_EXPIRY_MINUTES"""
        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            current_time = time.time()

            cancelled_buy = 0
            cancelled_sell = 0

            for order in open_orders:
                order_age_minutes = (current_time * 1000 - order['timestamp']) / 1000 / 60

                if order_age_minutes > ORDER_EXPIRY_MINUTES:
                    result = await asyncio.to_thread(self.trader.cancel_order, order['oid'])
                    if result.get('success'):
                        if order['side'] == 'buy':
                            cancelled_buy += 1
                        else:
                            cancelled_sell += 1

            if cancelled_buy > 0 or cancelled_sell > 0:
                print(f"Cancelled old orders: {cancelled_buy} buy, {cancelled_sell} sell (>{ORDER_EXPIRY_MINUTES}min)")

        except Exception as e:
            print(f"Cancel old orders error: {e}")

    async def run_single_iteration(self):
        """Single iteration for BTC"""
        try:
            mid_price = await self.get_mid_price()
            if not mid_price:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] No mid price data")
                return

            # Fetch all data once
            vol_multiplier = self.get_volatility_multiplier(mid_price)
            coin_balance, usdc_balance = await self.get_balance()
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)

            # Calculate inventory metrics
            coin_value = coin_balance * mid_price
            total_value = coin_value + usdc_balance
            coin_ratio = coin_value / total_value if total_value > 0 else 0.5

            deviation = coin_ratio - TARGET_COIN_RATIO
            status = "Balanced" if abs(deviation) < 0.1 else ("BTC Heavy" if deviation > 0 else "USDC Heavy")
            inventory_adj = deviation * INVENTORY_SKEW_MULTIPLIER

            # Display info
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {COIN} | Mid: ${mid_price:,.2f} | Vol: {vol_multiplier:.2f}x | Inv: {inventory_adj:+.2f} (ratio: {coin_ratio:.1%})")
            print(f"Balance: {coin_balance:.4f} BTC (${coin_value:,.0f}) | {usdc_balance:,.0f} USDC | {status} (target: {TARGET_COIN_RATIO:.1%})")

            # Count open orders
            buy_orders_count = len([o for o in open_orders if o['side'] == 'buy'])
            sell_orders_count = len([o for o in open_orders if o['side'] == 'sell'])

            # Place orders
            await self.place_orders('buy', mid_price, coin_ratio, coin_balance, usdc_balance, vol_multiplier, buy_orders_count)

            if coin_ratio >= MIN_SELL_RATIO:
                await self.place_orders('sell', mid_price, coin_ratio, coin_balance, usdc_balance, vol_multiplier, sell_orders_count)

            print(f"Open Orders - Buy: {buy_orders_count} | Sell: {sell_orders_count}")

        except Exception as e:
            print(f"  Error: {e}")

        await self.cancel_old_orders()

    async def run(self):
        """Main loop"""
        buy_spreads_str = " / ".join([f"-{s*100:.2f}%" for s in BUY_SPREADS])
        sell_spreads_str = " / ".join([f"+{s*100:.2f}%" for s in SELL_SPREADS])
        ratios_str = " / ".join([f"{r*100:.0f}%" for r in ORDER_RATIOS])

        print(f"Hyperliquid Spot MM | {COIN}")
        print(f"Order Size: ${ORDER_SIZE_USD} | Interval: {CHECK_INTERVAL}s")
        print(f"Buy Spreads: {buy_spreads_str}")
        print(f"Sell Spreads: {sell_spreads_str}")
        print(f"Ratios: {ratios_str}")
        print(f"Inventory Limits: {MIN_SELL_RATIO*100:.0f}% - {MAX_COIN_RATIO*100:.0f}% | Target: {TARGET_COIN_RATIO*100:.0f}%")
        print(f"Max Orders: {MAX_OPEN_ORDERS} | Expiry: {ORDER_EXPIRY_MINUTES}min | Skew: {INVENTORY_SKEW_MULTIPLIER}x")
        print(f"ATR: {ATR_INTERVAL}/{ATR_PERIOD} | Vol Range: {VOL_MULTIPLIER_MIN}x-{VOL_MULTIPLIER_MAX}x")
        print("=" * 60)

        while True:
            try:
                await self.run_single_iteration()
                await asyncio.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                print("\nShutting down...")
                break
            except Exception as e:
                print(f"\nMain loop error: {e}")
                await asyncio.sleep(5)


async def main():
    mm = MarketMaker()
    await mm.run()


if __name__ == "__main__":
    asyncio.run(main())
