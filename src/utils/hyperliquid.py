"""
Hyperliquid SDK 安全初始化

测试网 spotMeta API 可能返回 token index 越界的条目，
导致 Info.__init__ 在第 48 行抛出 IndexError。
此模块提供过滤后的安全初始化方式。
"""

import requests
from hyperliquid.info import Info


def safe_spot_meta(base_url: str) -> dict | None:
    """获取并过滤 spotMeta，移除 token index 越界的条目。"""
    try:
        resp = requests.post(f"{base_url}/info", json={"type": "spotMeta"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        tokens = data.get("tokens", [])
        count = len(tokens)
        original = data.get("universe", [])

        filtered = [e for e in original if e["tokens"][0] < count and e["tokens"][1] < count]
        if len(filtered) < len(original):
            data["universe"] = filtered

        return data
    except Exception:
        return None


def create_info(base_url: str, skip_ws: bool = True) -> Info:
    """安全创建 Info 实例，自动处理测试网 spotMeta 越界问题。"""
    meta = safe_spot_meta(base_url)
    if meta is not None:
        return Info(base_url, skip_ws=skip_ws, spot_meta=meta)
    return Info(base_url, skip_ws=skip_ws)
