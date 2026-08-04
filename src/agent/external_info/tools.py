"""
外部信息收集工具模块

提供加密货币市场新闻、监管政策、宏观经济等信息的搜索功能（使用原生 HTTP 请求直接调用 Exa API）。
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)


def search_crypto_market_news(
    query: str,
    exa_api_key: str,
    num_results: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """
    搜索加密货币市场新闻和信息

    Args:
        query: 搜索查询字符串
        exa_api_key: Exa API 密钥
        num_results: 返回结果数量
        start_date: 开始日期 (YYYY-MM-DD 格式)
        end_date: 结束日期 (YYYY-MM-DD 格式)

    Returns:
        格式化的搜索结果列表
    """
    url = "https://api.exa.ai/search"
    headers = {"x-api-key": exa_api_key, "content-type": "application/json"}

    payload = {"query": query, "numResults": num_results, "highlights": {"numSentences": 3}}

    if start_date:
        payload["startPublishedDate"] = f"{start_date}T00:00:00.000Z"
    if end_date:
        payload["endPublishedDate"] = f"{end_date}T23:59:59.000Z"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        documents = []
        for result in data.get("results", []):
            title = result.get("title") or "无标题"
            res_url = result.get("url") or ""
            published_date = result.get("publishedDate") or "未知"

            # Exa highlights is a list of strings
            highlights_list = result.get("highlights", [])
            highlights = (
                " ".join(highlights_list)
                if isinstance(highlights_list, list)
                else str(highlights_list)
            )
            if not highlights:
                highlights = result.get("text", "")[:500] or "无内容"

            formatted = f"""<source>
<title>{title}</title>
<url>{res_url}</url>
<published>{published_date}</published>
<highlights>{highlights}</highlights>
</source>"""
            documents.append(formatted)
        return documents
    except Exception as e:
        logger.warning(f"Exa search failed: {e}")
        return []


def search_crypto_regulatory_news(
    query: str,
    exa_api_key: str,
    num_results: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """
    搜索加密货币监管政策相关新闻
    """
    # 增强查询以聚焦监管内容
    enhanced_query = f"{query} regulation policy SEC compliance"
    return search_crypto_market_news(
        query=enhanced_query,
        exa_api_key=exa_api_key,
        num_results=num_results,
        start_date=start_date,
        end_date=end_date,
    )


def search_crypto_macro_news(
    query: str,
    exa_api_key: str,
    num_results: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """
    搜索影响加密货币的宏观经济新闻
    """
    # 增强查询以聚焦宏观经济内容
    enhanced_query = f"{query} Federal Reserve interest rate inflation economic impact"
    return search_crypto_market_news(
        query=enhanced_query,
        exa_api_key=exa_api_key,
        num_results=num_results,
        start_date=start_date,
        end_date=end_date,
    )


def create_period_search_queries(
    symbols: list[str], period_hours: int
) -> dict[str, list[dict[str, Any]]]:
    """
    为指定时间周期创建搜索查询

    Args:
        symbols: 关注的币种列表
        period_hours: 时间周期（小时）

    Returns:
        按主题分类的查询列表
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(hours=period_hours)

    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    date_range = f"{start_date_str} to {end_date_str}"

    # 构建查询
    queries = {"market_news": [], "regulatory": [], "macro": [], "industry": [], "sentiment": []}

    # 市场新闻查询 - 一次性查询所有币种
    if symbols:
        symbols_str = " ".join(symbols)
        queries["market_news"].append(
            {
                "query": f"cryptocurrency {symbols_str} market news price analysis {date_range}",
                "start_date": start_date_str,
                "end_date": end_date_str,
            }
        )

    # 监管政策查询
    queries["regulatory"].append(
        {
            "query": f"cryptocurrency regulation policy {date_range}",
            "start_date": start_date_str,
            "end_date": end_date_str,
        }
    )

    # 宏观经济查询
    queries["macro"].append(
        {
            "query": f"Federal Reserve crypto impact {date_range}",
            "start_date": start_date_str,
            "end_date": end_date_str,
        }
    )

    # 行业动态查询
    queries["industry"].append(
        {
            "query": f"blockchain technology crypto exchange news {date_range}",
            "start_date": start_date_str,
            "end_date": end_date_str,
        }
    )

    # 市场情绪查询
    queries["sentiment"].append(
        {
            "query": f"Bitcoin whale activity institutional investment {date_range}",
            "start_date": start_date_str,
            "end_date": end_date_str,
        }
    )

    return queries
