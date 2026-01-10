import requests
from hyperliquid.utils import constants
import hyperliquid_utils

API_URL = 'https://api.hyperliquid.xyz/info'


class HyperliquidTrader:
    def __init__(self, coin: str, symbol: str, tick_size: float = 1, size_decimals: int = 5, dex: str = None):
        self.coin = coin
        self.symbol = symbol
        self.tick_size = tick_size
        self.size_decimals = size_decimals
        self.dex = dex
        api_url = constants.MAINNET_API_URL
        perp_dexs = [dex] if dex else None
        self.address, self.info, self.exchange = hyperliquid_utils.setup(api_url, skip_ws=True, perp_dexs=perp_dexs)

    @staticmethod
    def _execute_order(method, *args, **kwargs):
        """Execute an order and handle the response"""
        try:
            result = method(*args, **kwargs)

            if result["status"] != "ok":
                return {"success": False, "error": result.get("response")}

            for status in result["response"]["data"]["statuses"]:
                if "filled" in status:
                    filled = status["filled"]
                    print(f"Filled: {filled['totalSz']} @ ${filled['avgPx']}")

            return {"success": True, "data": result}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _api_request(self, request_type, exclude_dex=False, **extra_params):
        """Common method for API POST requests"""
        try:
            payload = {'type': request_type, 'user': self.address}
            if self.dex and not exclude_dex:
                payload['dex'] = self.dex
            payload.update(extra_params)

            response = requests.post(
                API_URL,
                headers={'Content-Type': 'application/json'},
                json=payload
            )

            if response.status_code != 200:
                print(f"API error ({request_type}): {response.status_code}")
                return None

            return response.json()

        except Exception as e:
            print(f"API request error ({request_type}): {e}")
            return None

    def spot_buy(self, quantity, price, order_type="Gtc"):
        """Place spot buy order"""
        return self._execute_order(
            self.exchange.order,
            self.symbol, True, quantity, price, {"limit": {"tif": order_type}}
        )

    def spot_sell(self, quantity, price, order_type="Gtc"):
        """Place spot sell order"""
        return self._execute_order(
            self.exchange.order,
            self.symbol, False, quantity, price, {"limit": {"tif": order_type}}
        )

    def cancel_order(self, oid):
        """Cancel an order by oid"""
        try:
            result = self.exchange.cancel(self.symbol, oid)
            if result["status"] == "ok":
                return {"success": True}
            else:
                return {"success": False, "error": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_spot_balance(self):
        """Get spot balances via spotClearinghouseState API"""
        data = self._api_request('spotClearinghouseState')

        if not data:
            return {}

        balances = {}
        for balance in data.get('balances', []):
            coin = balance['coin']
            total = float(balance['total'])
            balances[coin] = total

        return balances

    def get_mid_price(self):
        """Get mid price from allMids API"""
        try:
            payload = {'type': 'allMids'}
            if self.dex:
                payload['dex'] = self.dex

            response = requests.post(
                API_URL,
                headers={'Content-Type': 'application/json'},
                json=payload
            )

            if response.status_code != 200:
                return None

            data = response.json()

            key = self.symbol if self.symbol.startswith('@') else self.coin
            mid_price = float(data.get(key, 0))
            return mid_price if mid_price > 0 else None
        except Exception as e:
            print(f"Mid price error: {e}")
            return None

    def get_open_orders(self):
        """Get open orders from exchange"""
        data = self._api_request('openOrders')

        if not data:
            return []

        # Filter for spot orders
        spot_orders = []
        for order in data:
            if order.get('coin') == self.symbol:
                spot_orders.append({
                    'oid': order['oid'],
                    'side': 'buy' if order['side'] == 'B' else 'sell',
                    'price': float(order['limitPx']),
                    'size': float(order['sz']),
                    'timestamp': order['timestamp']
                })

        return spot_orders

    # ===== Perp Methods =====

    def perp_long(self, quantity, price, order_type="Gtc"):
        """Place perp long order"""
        return self._execute_order(
            self.exchange.order,
            self.coin, True, quantity, price, {"limit": {"tif": order_type}}
        )

    def perp_short(self, quantity, price, order_type="Gtc"):
        """Place perp short order"""
        return self._execute_order(
            self.exchange.order,
            self.coin, False, quantity, price, {"limit": {"tif": order_type}}
        )

    def get_perp_position(self):
        """Get perp position via clearinghouseState API"""
        data = self._api_request('clearinghouseState')

        if not data:
            return None

        for position in data.get('assetPositions', []):
            pos = position.get('position', {})
            if pos.get('coin') == self.symbol:
                return {
                    'size': float(pos.get('szi', 0)),
                    'entry_price': float(pos.get('entryPx', 0)),
                    'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                    'margin_used': float(pos.get('marginUsed', 0))
                }

        return {'size': 0, 'entry_price': 0, 'unrealized_pnl': 0, 'margin_used': 0}

    def get_perp_balance(self):
        """Get perp account balance (margin)"""
        data = self._api_request('clearinghouseState', exclude_dex=True)

        if not data:
            return 0

        return float(data.get('marginSummary', {}).get('accountValue', 0))
