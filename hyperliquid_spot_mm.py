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
INVENTORY_SKEW_MULTIPLIER = 1.5

# ATR settings
ATR_INTERVAL = '5m'
ATR_PERIOD = 14
BASE_SPREAD = 0.001  # 0.1% 기준
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

    @staticmethod
    def _display_orders(orders_list, order_type, limit=5):
        """Helper to display order list"""
        if not orders_list:
            return

        print(f"  {order_type} Orders:")
        sorted_orders = sorted(orders_list, key=lambda x: x['price'], reverse=(order_type == 'Buy'))
        for order in sorted_orders[:limit]:
            age = (time.time() * 1000 - order['timestamp']) / 1000 / 60
            print(f"    {order['size']:.6f} @ ${order['price']:.2f} (OID: {order['oid']}, {age:.1f}min ago)")

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

    async def calculate_inventory_ratio(self, mid_price):
        """Calculate coin to total value ratio"""
        coin_balance, usdc_balance = await self.get_balance()

        if mid_price <= 0:
            return 0.5, coin_balance, usdc_balance

        coin_value = coin_balance * mid_price
        total_value = coin_value + usdc_balance
        coin_ratio = coin_value / total_value if total_value > 0 else 0.5

        return coin_ratio, coin_balance, usdc_balance

    async def place_buy_orders(self, mid_price, vol_multiplier=1.0):
        """Place 5-tier buy orders with inventory-adjusted spreads"""
        if not self.trading_enabled:
            return False

        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            buy_orders_count = len([o for o in open_orders if o['side'] == 'buy'])

            if buy_orders_count >= MAX_OPEN_ORDERS:
                print(f"  Max open buy orders reached ({buy_orders_count}/{MAX_OPEN_ORDERS}), skipping")
                return False
        except:
            pass

        coin_ratio, coin_balance, usdc_balance = await self.calculate_inventory_ratio(mid_price)

        if coin_ratio >= MAX_COIN_RATIO:
            print(f"  Skip buy (coin ratio {coin_ratio:.1%} >= {MAX_COIN_RATIO:.1%})")
            return False

        if usdc_balance < ORDER_SIZE_USD:
            print(f"  Skip buy (USDC {usdc_balance:.2f} < {ORDER_SIZE_USD})")
            return False

        buy_spreads, _ = self.calculate_inventory_adjusted_spreads(coin_ratio, vol_multiplier)
        orders = []

        for ratio, spread in zip(ORDER_RATIOS, buy_spreads):
            usd_amount = ORDER_SIZE_USD * ratio
            price = self.format_price(mid_price * (1 - spread))
            qty = self.format_quantity(usd_amount / price)
            orders.append((qty, price))

        try:
            results = await self._place_orders_sequential(self.trader.spot_buy, orders)
            success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))

            print(f"BUY Orders (ratio: {coin_ratio:.1%}):")
            for i, ((qty, price), spread) in enumerate(zip(orders, buy_spreads), 1):
                print(f"  BUY-{i}: {qty} @ {price} (-{spread * 100:.2f}%)")
            print(f"  Success: {success_count}/5")

            return success_count > 0

        except Exception as e:
            print(f"Buy orders error: {e}")
            return False

    async def place_sell_orders(self, mid_price, vol_multiplier=1.0):
        """Place 5-tier sell orders with inventory-adjusted spreads"""
        if not self.trading_enabled:
            return False

        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            sell_orders_count = len([o for o in open_orders if o['side'] == 'sell'])

            if sell_orders_count >= MAX_OPEN_ORDERS:
                print(f"  Max open sell orders reached ({sell_orders_count}/{MAX_OPEN_ORDERS}), skipping")
                return False
        except:
            pass

        coin_ratio, coin_balance, usdc_balance = await self.calculate_inventory_ratio(mid_price)

        if coin_balance < 0.0001:
            print(f"  Skip sell (BTC balance {coin_balance:.6f} too low)")
            return False

        _, sell_spreads = self.calculate_inventory_adjusted_spreads(coin_ratio, vol_multiplier)
        orders = []

        for ratio, spread in zip(ORDER_RATIOS, sell_spreads):
            usd_amount = ORDER_SIZE_USD * ratio
            price = self.format_price(mid_price * (1 + spread))
            qty = self.format_quantity(usd_amount / price)
            orders.append((qty, price))

        total_sell_qty = sum(qty for qty, _ in orders)
        if total_sell_qty > coin_balance:
            print(f"  Insufficient BTC (need {total_sell_qty:.6f}, have {coin_balance:.6f}), skipping")
            return False

        try:
            results = await self._place_orders_sequential(self.trader.spot_sell, orders)
            success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))

            print(f"SELL Orders (ratio: {coin_ratio:.1%}):")
            for i, ((qty, price), spread) in enumerate(zip(orders, sell_spreads), 1):
                print(f"  SELL-{i}: {qty} @ {price} (+{spread * 100:.2f}%)")
            print(f"  Success: {success_count}/5")

            return success_count > 0

        except Exception as e:
            print(f"Sell orders error: {e}")
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
        print(f"\n{'=' * 60}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {COIN}...")

        try:
            mid_price = await self.get_mid_price()

            if not mid_price:
                print(f"  No mid price data")
                return

            print(f"  Mid Price: ${mid_price:.4f}")

            vol_multiplier = self.get_volatility_multiplier(mid_price)
            print(f"  Volatility Multiplier: {vol_multiplier:.2f}x")

            coin_ratio, coin_balance, usdc_balance = await self.calculate_inventory_ratio(mid_price)
            coin_value = coin_balance * mid_price

            deviation = coin_ratio - TARGET_COIN_RATIO
            status = "Balanced" if abs(deviation) < 0.1 else ("BTC Heavy" if deviation > 0 else "USDC Heavy")

            print(f"  Balance: {coin_balance:.4f} BTC (${coin_value:.2f}) | {usdc_balance:.2f} USDC")
            print(f"  {status} | Ratio: {coin_ratio:.1%} (target: {TARGET_COIN_RATIO:.1%})")

            await self.place_buy_orders(mid_price, vol_multiplier)

            if coin_ratio >= MIN_SELL_RATIO:
                await self.place_sell_orders(mid_price, vol_multiplier)

        except Exception as e:
            print(f"  Price error: {e}")

        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            buy_orders = [o for o in open_orders if o['side'] == 'buy']
            sell_orders = [o for o in open_orders if o['side'] == 'sell']

            print(f"\nOpen Orders - Buy: {len(buy_orders)} | Sell: {len(sell_orders)}")

        except Exception as e:
            print(f"\nOpen Orders: Unable to fetch - {e}")

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
