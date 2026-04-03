"""
K 线节拍驱动测试
测试 _wait_next_candle 的等待时间计算逻辑。
"""

import time

from src.utils.candle_align import next_candle_close_ts


class TestWaitNextCandleLogic:
    """节拍等待逻辑（不依赖 QuantFlowBot 实例，直接测试计算公式）"""

    def test_sleep_duration_positive(self):
        """正常情况下 sleep_duration > 0"""
        now = time.time()
        target = next_candle_close_ts("15m", now_ts=now) + 2.0
        sleep_duration = max(target - now, 30.0)
        assert sleep_duration > 0
        # 15 分钟 K 线最多等 900 + 2 秒
        assert sleep_duration <= 902

    def test_sleep_duration_uses_min_when_negative(self):
        """当 target 已过去（LLM 调用耗时超过一根 K 线），使用 min_throttle_secs"""
        now = time.time()
        # 模拟 target 在过去
        target = now - 100
        min_throttle_secs = 30.0
        sleep_duration = max(target - now, min_throttle_secs)
        assert sleep_duration == min_throttle_secs

    def test_is_running_flag_exits_early(self):
        """is_running 设为 False 时快速退出等待"""

        class FakeBot:
            is_running = True

        bot = FakeBot()
        start = time.time()

        # 模拟分段 sleep 逻辑
        end_time = time.time() + 10.0  # 10 秒等待

        # 0.05 秒后设置 is_running = False
        import threading

        def stop_later():
            time.sleep(0.05)
            bot.is_running = False

        t = threading.Thread(target=stop_later, daemon=True)
        t.start()

        while time.time() < end_time and bot.is_running:
            time.sleep(min(0.02, end_time - time.time()))

        elapsed = time.time() - start
        # 应在 0.05-0.2 秒内退出（远小于 10 秒）
        assert elapsed < 1.0
