"""
网格交易管理器 (动态调节版)
支持网格同步、AI 止盈止损和状态持久化
"""

import time
import json
import os
from typing import Dict, Any, List, Optional
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger

class GridManager:
    """管理网格订单的动态同步"""

    def __init__(self, order_manager: OrderManager, logger: TradingLogger, state_file: str = "grid_state.json", notifier=None):
        self.order_manager = order_manager
        self.logger = logger
        self.notifier = notifier
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                try:
                    data = json.load(f)
                    if "active_grids" not in data: data = {"active_grids": {}}
                    return data
                except: pass
        return {"active_grids": {}}

    def _save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def sync_grid(self, symbol: str, ai_config: Dict[str, Any]):
        """
        核心逻辑：根据 AI 最新的决策，同步现实中的网格状态。
        """
        action = ai_config.get("action")
        if action != "UPDATE_GRID":
            self.logger.print_info(f"[{symbol} Grid] AI 保持当前状态或建议等待。")
            return

        # 兼容两种格式：参数在根目录或在 parameters 下
        params = ai_config.get("parameters", ai_config)
        new_lower = params.get("lower_price")
        new_upper = params.get("upper_price")
        new_num = params.get("grid_num", 10)
        # 增加安全检查
        try:
            new_num = int(new_num)
            if new_num <= 0: new_num = 10
        except:
            new_num = 10
            
        new_amount = params.get("amount_per_grid")
        tp_ratio = params.get("tp_ratio")
        sl_ratio = params.get("sl_ratio")

        if new_lower is None or new_upper is None or new_amount is None:
            self.logger.print_error(f"   [Grid] ❌ 配置缺失: lower={new_lower}, upper={new_upper}, amount={new_amount}")
            return

        # 防止 AI 抽风输出 -1
        if new_upper <= 0 or new_lower <= 0:
            self.logger.print_error(f"   [Grid] ❌ 非法价格区间: ${new_lower} - ${new_upper}")
            return

        self.logger.print_section(f"🔄 动态调整 {symbol} 网格", style="bold cyan")
        self.logger.print_info(f"AI 新区间: ${new_lower} - ${new_upper} | TP: {tp_ratio} SL: {sl_ratio}")

        # 1. 彻底清理旧订单
        self._cancel_all_orders(symbol)

        # 2. 计算新价格分布
        prices = self._calculate_grid_prices(new_lower, new_upper, new_num, ai_config.get("grid_type", "GEOMETRIC"))
        current_price = self.order_manager.client.get_current_price(symbol)
        
        buy_orders = []
        sell_orders = []

        # 3. 重新布置
        for i, p in enumerate(prices):
            if i > 0: time.sleep(1.0) # 防限流

            try:
                if p < current_price:
                    res = self.order_manager.execute_long_limit(symbol, new_amount, p, tp_ratio=tp_ratio, sl_ratio=sl_ratio)
                    if res and res.get('success'):
                        oid = self._extract_oid(res['limit_order'])
                        if oid:
                            buy_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 买单挂载: ${p}")
                elif p > current_price:
                    res = self.order_manager.execute_short_limit(symbol, new_amount, p, tp_ratio=tp_ratio, sl_ratio=sl_ratio)
                    if res and res.get('success'):
                        oid = self._extract_oid(res['limit_order'])
                        if oid:
                            sell_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 卖单挂载: ${p}")
            except Exception as e:
                self.logger.print_error(f"   [Grid] 下单异常 @ ${p}: {e}")

        # 4. 更新状态
        self.state["active_grids"][symbol] = {
            "config": ai_config,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "last_sync": time.time()
        }
        self._save_state()
        self.logger.print_info(f"✅ {symbol} 网格调整完成。")

        # 发送通知
        if self.notifier:
            self.notifier.notify_grid_update(
                symbol=symbol,
                lower=new_lower,
                upper=new_upper,
                num=new_num,
                amount=new_amount,
                tp=tp_ratio,
                sl=sl_ratio,
                buy_count=len(buy_orders),
                sell_count=len(sell_orders),
                reason=ai_config.get("reason", "N/A")
            )

    def _extract_oid(self, limit_order_res: Dict[str, Any]) -> Optional[int]:
        try:
            # 兼容 SDK 原始返回格式
            if 'response' in limit_order_res:
                return limit_order_res['response']['data']['statuses'][0]['resting']['oid']
            return None
        except: return None

    def _calculate_grid_prices(self, lower: float, upper: float, num: int, grid_type: str) -> List[float]:
        if num < 2: return [lower]
        # 确保输入是实数而非复数
        try:
            if hasattr(lower, "real"): lower = float(lower.real)
            if hasattr(upper, "real"): upper = float(upper.real)
        except: pass

        prices = []
        if grid_type == "ARITHMETIC":
            diff = (upper - lower) / (num - 1)
            for i in range(num):
                prices.append(round(lower + i * diff, 1))
        else: # GEOMETRIC
            # 增加安全检查
            if lower <= 0 or upper <= 0: return [lower]
            ratio = (upper / lower) ** (1 / (num - 1))
            for i in range(num):
                prices.append(round(lower * (ratio ** i), 1))
        return prices

    def _cancel_all_orders(self, symbol: str):
        grid = self.state["active_grids"].get(symbol)
        if not grid: return
        
        oids = [o['oid'] for o in grid.get("buy_orders", []) if isinstance(o, dict)] + \
               [o['oid'] for o in grid.get("sell_orders", []) if isinstance(o, dict)]
        for oid in oids:
            try: self.order_manager.client.cancel_order(symbol, oid)
            except: pass
        
        del self.state["active_grids"][symbol]
        self._save_state()

    def get_grid_summary(self, symbol: str) -> str:
        grid = self.state["active_grids"].get(symbol)
        if not grid: return "目前无运行中的网格。"
        
        config = grid['config']
        params = config.get("parameters", config)
        return (f"当前正在运行 {symbol} 天地单网格：\n"
                f"- 区间: ${params.get('lower_price', 'N/A')} - ${params.get('upper_price', 'N/A')}\n"
                f"- 止盈比例: {params.get('tp_ratio', 'N/A')}\n"
                f"- 待成交买单: {len(grid['buy_orders'])} 个\n"
                f"- 待成交卖单: {len(grid['sell_orders'])} 个")
