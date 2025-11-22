import asyncio
from datetime import datetime
import time

import requests
import numpy as np
import hyperliquid_trade

API_URL = 'https://api.hyperliquid.xyz/info'

DEX = 'xyz'
COIN = f'{DEX}:XYZ100'
SIZE_DECIMALS = 4
TICK_SIZE = 1

ORDER_SIZE_USD = 500
CHECK_INTERVAL = 60
MAX_OPEN_ORDERS = 50
ORDER_EXPIRY_MINUTES = 15

# Spreads and ratios for 5-tier orders
LONG_SPREADS = [0.001, 0.002, 0.003, 0.004, 0.005]
SHORT_SPREADS = [0.001, 0.002, 0.003, 0.004, 0.005]
ORDER_RATIOS = [0.50, 0.20, 0.10, 0.10, 0.10]

# Position limits
MAX_POSITION_USD = 10000
INVENTORY_SKEW_MULTIPLIER = 1.5

# ATR settings
ATR_INTERVAL = '5m'
ATR_PERIOD = 14
BASE_SPREAD = 0.001
VOL_MULTIPLIER_MIN = 0.5
VOL_MULTIPLIER_MAX = 2.0


class PerpMarketMaker:
    def __init__(self):
        try:
            self.trader = hyperliquid_trade.HyperliquidTrader(
                coin=COIN,
                symbol=COIN,
                tick_size=TICK_SIZE,
                size_decimals=SIZE_DECIMALS,
                dex=DEX
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

    def calculate_inventory_adjusted_spreads(self, position_ratio, vol_multiplier=1.0):
        """Adjust spreads based on position imbalance and volatility"""
        adj = position_ratio * INVENTORY_SKEW_MULTIPLIER
        long_spreads = [max(0.0001, s * (1 + adj) * vol_multiplier) for s in LONG_SPREADS]
        short_spreads = [max(0.0001, s * (1 - adj) * vol_multiplier) for s in SHORT_SPREADS]
        return long_spreads, short_spreads

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
        sorted_orders = sorted(orders_list, key=lambda x: x['price'], reverse=(order_type == 'Long'))
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

    async def get_position(self):
        """Get current perp position"""
        try:
            position = await asyncio.to_thread(self.trader.get_perp_position)
            return position
        except Exception as e:
            print(f"Position error: {e}")
            return {'size': 0, 'entry_price': 0, 'unrealized_pnl': 0, 'margin_used': 0}

    async def get_balance(self):
        """Get account balance"""
        try:
            balance = await asyncio.to_thread(self.trader.get_perp_balance)
            return balance
        except Exception as e:
            print(f"Balance error: {e}")
            return 0

    async def calculate_position_ratio(self, mid_price):
        """Calculate position to max position ratio"""
        position = await self.get_position()
        position_size = position['size']

        if mid_price <= 0:
            return 0, position

        position_value = abs(position_size * mid_price)
        position_ratio = position_value / MAX_POSITION_USD if MAX_POSITION_USD > 0 else 0

        # Negative ratio for short, positive for long
        if position_size < 0:
            position_ratio = -position_ratio

        return position_ratio, position

    async def place_long_orders(self, mid_price, vol_multiplier=1.0):
        """Place 5-tier long orders with inventory-adjusted spreads"""
        if not self.trading_enabled:
            return False

        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            buy_orders_count = len([o for o in open_orders if o['side'] == 'buy'])

            if buy_orders_count >= MAX_OPEN_ORDERS:
                print(f"  � Max open long orders reached ({buy_orders_count}/{MAX_OPEN_ORDERS}), skipping")
                return False
        except:
            pass

        position_ratio, position = await self.calculate_position_ratio(mid_price)
        position_value = position['size'] * mid_price

        # Skip if already at max long position
        if position_value >= MAX_POSITION_USD:
            print(f"  Skip long (position ${position_value:.2f} >= max ${MAX_POSITION_USD})")
            return False

        long_spreads, _ = self.calculate_inventory_adjusted_spreads(position_ratio, vol_multiplier)
        orders = []

        for ratio, spread in zip(ORDER_RATIOS, long_spreads):
            usd_amount = ORDER_SIZE_USD * ratio
            price = self.format_price(mid_price * (1 - spread))
            qty = self.format_quantity(usd_amount / price)
            orders.append((qty, price))

        try:
            results = await self._place_orders_sequential(self.trader.perp_long, orders)
            success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))

            print(f"LONG Orders (pos ratio: {position_ratio:.1%}, adj spreads):")
            for i, ((qty, price), spread) in enumerate(zip(orders, long_spreads), 1):
                print(f"LONG-{i}: {qty} @ {price} (-{spread * 100:.2f}%)")
            print(f"Success: {success_count}/5")

            return success_count > 0

        except Exception as e:
            print(f"L Long orders error: {e}")
            return False

    async def place_short_orders(self, mid_price, vol_multiplier=1.0):
        """Place 5-tier short orders with inventory-adjusted spreads"""
        if not self.trading_enabled:
            return False

        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            sell_orders_count = len([o for o in open_orders if o['side'] == 'sell'])

            if sell_orders_count >= MAX_OPEN_ORDERS:
                print(f"  � Max open short orders reached ({sell_orders_count}/{MAX_OPEN_ORDERS}), skipping")
                return False
        except:
            pass

        position_ratio, position = await self.calculate_position_ratio(mid_price)
        position_value = position['size'] * mid_price

        # Skip if already at max short position
        if position_value <= -MAX_POSITION_USD:
            print(f"  Skip short (position ${position_value:.2f} <= -max ${MAX_POSITION_USD})")
            return False

        _, short_spreads = self.calculate_inventory_adjusted_spreads(position_ratio, vol_multiplier)
        orders = []

        for ratio, spread in zip(ORDER_RATIOS, short_spreads):
            usd_amount = ORDER_SIZE_USD * ratio
            price = self.format_price(mid_price * (1 + spread))
            qty = self.format_quantity(usd_amount / price)
            orders.append((qty, price))

        try:
            results = await self._place_orders_sequential(self.trader.perp_short, orders)
            success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))

            print(f"=SHORT Orders (pos ratio: {position_ratio:.1%}, adj spreads):")
            for i, ((qty, price), spread) in enumerate(zip(orders, short_spreads), 1):
                print(f"SHORT-{i}: {qty} @ {price} (+{spread * 100:.2f}%)")
            print(f"Success: {success_count}/5")

            return success_count > 0

        except Exception as e:
            print(f"L Short orders error: {e}")
            return False

    async def cancel_old_orders(self):
        """Cancel orders older than ORDER_EXPIRY_MINUTES"""
        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            current_time = time.time()

            cancelled_long = 0
            cancelled_short = 0

            for order in open_orders:
                order_age_minutes = (current_time * 1000 - order['timestamp']) / 1000 / 60

                if order_age_minutes > ORDER_EXPIRY_MINUTES:
                    result = await asyncio.to_thread(self.trader.cancel_order, order['oid'])
                    if result.get('success'):
                        if order['side'] == 'buy':
                            cancelled_long += 1
                        else:
                            cancelled_short += 1

            if cancelled_long > 0 or cancelled_short > 0:
                print(f"� Cancelled old orders: {cancelled_long} long, {cancelled_short} short (>{ORDER_EXPIRY_MINUTES}min)")

        except Exception as e:
            print(f"L Cancel old orders error: {e}")

    async def run_single_iteration(self):
        """Single iteration for BTC perp"""
        print(f"\n{'=' * 60}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {COIN} Perp...")

        try:
            mid_price = await self.get_mid_price()

            if not mid_price:
                print(f"  No mid price data")
                return

            print(f"  Mid Price: ${mid_price:.4f}")

            vol_multiplier = self.get_volatility_multiplier(mid_price)
            print(f"  Volatility Multiplier: {vol_multiplier:.2f}x")

            # Display current position and balance
            position = await self.get_position()
            balance = await self.get_balance()
            position_value = position['size'] * mid_price

            position_status = "Neutral" if abs(position['size']) < 0.0001 else ("Long" if position['size'] > 0 else "Short")

            print(f"Balance: ${balance:.2f}")
            print(f"Position: {position['size']:.6f} {COIN} (${position_value:.2f})")
            print(f"  {position_status} | Entry: ${position['entry_price']:.2f} | PnL: ${position['unrealized_pnl']:.2f}")

            # Place long and short orders
            await self.place_long_orders(mid_price, vol_multiplier)
            await self.place_short_orders(mid_price, vol_multiplier)

        except Exception as e:
            print(f"  Price error: {e}")

        # Display current open orders
        try:
            open_orders = await asyncio.to_thread(self.trader.get_open_orders)
            long_orders = [o for o in open_orders if o['side'] == 'buy']
            short_orders = [o for o in open_orders if o['side'] == 'sell']

            print(f"\nOpen Orders - Long: {len(long_orders)} | Short: {len(short_orders)}")

        except Exception as e:
            print(f"\nOpen Orders: Unable to fetch - {e}")

        # Cancel old orders
        await self.cancel_old_orders()

    async def run(self):
        """Main loop"""
        long_spreads_str = " / ".join([f"-{s*100:.2f}%" for s in LONG_SPREADS])
        short_spreads_str = " / ".join([f"+{s*100:.2f}%" for s in SHORT_SPREADS])
        ratios_str = " / ".join([f"{r*100:.0f}%" for r in ORDER_RATIOS])

        print(f"Hyperliquid Perp MM | {COIN}")
        print(f"Order Size: ${ORDER_SIZE_USD} | Interval: {CHECK_INTERVAL}s")
        print(f"Long Spreads: {long_spreads_str}")
        print(f"Short Spreads: {short_spreads_str}")
        print(f"Ratios: {ratios_str}")
        print(f"Max Position: ${MAX_POSITION_USD} | Max Orders: {MAX_OPEN_ORDERS} | Expiry: {ORDER_EXPIRY_MINUTES}min")
        print(f"Skew: {INVENTORY_SKEW_MULTIPLIER}x | ATR: {ATR_INTERVAL}/{ATR_PERIOD} | Vol Range: {VOL_MULTIPLIER_MIN}x-{VOL_MULTIPLIER_MAX}x")
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
    mm = PerpMarketMaker()
    await mm.run()


if __name__ == "__main__":
    asyncio.run(main())
