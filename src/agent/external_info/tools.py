"""
外部信息收集工具模块

使用 LangChain 和 Exa 集成的搜索工具。
提供加密货币市场新闻、监管政策、宏观经济等信息的搜索功能。
"""

from datetime import datetime, timedelta
from typing import Any

from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langchain_exa import ExaSearchRetriever


@tool
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

    # 初始化 Exa 检索器
    retriever_kwargs = {
        "k": num_results,
        "highlights": True,
        "text_length_limit": 1000,
        "exa_api_key": exa_api_key,
    }

    # 添加日期过滤
    if start_date:
        retriever_kwargs["start_published_date"] = start_date
    if end_date:
        retriever_kwargs["end_published_date"] = end_date

    retriever = ExaSearchRetriever(**retriever_kwargs)

    # 定义文档格式化模板
    document_prompt = PromptTemplate.from_template(
        """<source>
<title>{title}</title>
<url>{url}</url>
<published>{published_date}</published>
<highlights>{highlights}</highlights>
</source>"""
    )

    # 创建文档处理链
    document_chain = (
        RunnableLambda(
            lambda doc: {
                "title": doc.metadata.get("title", "无标题"),
                "url": doc.metadata.get("url", ""),
                "published_date": doc.metadata.get("published_date", "未知"),
                "highlights": doc.metadata.get("highlights", doc.page_content[:500] or "无内容"),
            }
        )
        | document_prompt
    )

    # 执行检索和处理链
    retrieval_chain = retriever | document_chain.map()

    try:
        documents = retrieval_chain.invoke(query)
        return documents
    except Exception:
        return []


@tool
def search_crypto_regulatory_news(
    query: str,
    exa_api_key: str,
    num_results: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """
    搜索加密货币监管政策相关新闻

    Args:
        query: 搜索查询字符串
        exa_api_key: Exa API 密钥
        num_results: 返回结果数量
        start_date: 开始日期 (YYYY-MM-DD 格式)
        end_date: 结束日期 (YYYY-MM-DD 格式)

    Returns:
        格式化的搜索结果列表
    """
    # 增强查询以聚焦监管内容
    enhanced_query = f"{query} regulation policy SEC compliance"
    return search_crypto_market_news.invoke(
        {
            "query": enhanced_query,
            "exa_api_key": exa_api_key,
            "num_results": num_results,
            "start_date": start_date,
            "end_date": end_date,
        }
    )


@tool
def search_crypto_macro_news(
    query: str,
    exa_api_key: str,
    num_results: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """
    搜索影响加密货币的宏观经济新闻

    Args:
        query: 搜索查询字符串
        exa_api_key: Exa API 密钥
        num_results: 返回结果数量
        start_date: 开始日期 (YYYY-MM-DD 格式)
        end_date: 结束日期 (YYYY-MM-DD 格式)

    Returns:
        格式化的搜索结果列表
    """
    # 增强查询以聚焦宏观经济内容
    enhanced_query = f"{query} Federal Reserve interest rate inflation economic impact"
    return search_crypto_market_news.invoke(
        {
            "query": enhanced_query,
            "exa_api_key": exa_api_key,
            "num_results": num_results,
            "start_date": start_date,
            "end_date": end_date,
        }
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
