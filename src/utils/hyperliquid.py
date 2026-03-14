"""
Hyperliquid SDK 工具函数
解决 SDK 初始化时因测试网 spotMeta 数据不一致导致的 IndexError
"""

import requests
from hyperliquid.info import Info
from hyperliquid.utils.types import SpotMeta


def get_safe_spot_meta(base_url: str) -> SpotMeta | None:
    """
    获取并过滤 spotMeta，移除 token index 越界的现货条目。

    Hyperliquid 测试网的 spotMeta API 可能返回 universe 中引用了 tokens 列表中
    不存在的 index（如 base=1576 但 tokens 只有 1575 个），导致 SDK 初始化时
    在 info.py:48 处抛出 IndexError。
    """
    try:
        resp = requests.post(
            f"{base_url}/info",
            json={"type": "spotMeta"},
            timeout=10,
        )
        resp.raise_for_status()
        spot_meta = resp.json()

        tokens = spot_meta.get("tokens", [])
        token_count = len(tokens)
        original_universe = spot_meta.get("universe", [])

        filtered_universe = []
        skipped = 0
        for entry in original_universe:
            base, quote = entry["tokens"]
            if base < token_count and quote < token_count:
                filtered_universe.append(entry)
            else:
                skipped += 1

        if skipped > 0:
            print(f"⚠️ spotMeta 过滤了 {skipped} 个 token index 越界的现货条目")
            spot_meta["universe"] = filtered_universe

        return spot_meta
    except Exception:
        return None


def create_info(base_url: str, skip_ws: bool = True, spot_meta: SpotMeta | None = None) -> Info:
    """
    安全创建 Info 实例。

    如果未传入 spot_meta，则自动获取并过滤越界条目后传给 Info 构造函数。
    """
    if spot_meta is None:
        spot_meta = get_safe_spot_meta(base_url)

    if spot_meta is not None:
        return Info(base_url, skip_ws=skip_ws, spot_meta=spot_meta)

    # 回退：预处理失败时直接用默认方式（主网通常不会有问题）
    return Info(base_url, skip_ws=skip_ws)
