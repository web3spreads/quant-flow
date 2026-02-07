"""
外部信息收集 Agent 测试
测试 LangChain 工作流集成
"""

import os
import sys
from pathlib import Path


# 添加项目根目录到路径
def find_project_root(marker="pyproject.toml"):
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / marker).is_file():
            return current
        current = current.parent
    raise FileNotFoundError(f"Could not find {marker} in any parent directory of {__file__}")


project_root = find_project_root()
sys.path.insert(0, str(project_root))

from src.agent.external_info_agent import ExternalInfoAgent
from src.utils.logger import get_logger


def test_langchain_workflow():
    """测试 LangChain 工作流"""
    print("=" * 60)
    print("测试外部信息收集 Agent")
    print("=" * 60)

    logger = get_logger()

    # 从环境变量读取 API 密钥
    exa_api_key = os.getenv("EXA_API_KEY")
    if not exa_api_key:
        print("❌ 未设置 EXA_API_KEY 环境变量")
        return

    # 创建 Agent
    agent = ExternalInfoAgent(
        logger=logger,
        openai_api_base=os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
        exa_api_key=exa_api_key,  # 必须显式传入
        symbols=["BTC", "ETH"],
        interval_hours=3.0,  # 使用 3 小时间隔
    )

    # 测试收集
    print("\n测试收集市场信息（3 小时间隔）...")
    saved_file = agent.collect_and_save()

    if saved_file:
        print("\n✅ 成功生成报告:")
        print(f"  文件: {saved_file}")

        # 获取摘要
        summary = agent.get_latest_summary(symbols=["BTC", "ETH"], max_length=500)
        print(f"\n报告摘要:\n{summary}")
    else:
        print("\n❌ 未生成任何报告")

    # 获取报告状态
    print("\n报告状态:")
    status = agent.get_report_status()
    print(f"  总文件数: {status.get('total_files', 0)}")
    print(f"  最新文件: {status.get('latest_file', 'N/A')}")


def test_tools_directly():
    """直接测试工具"""
    print("\n" + "=" * 60)
    print("直接测试 LangChain 工具")
    print("=" * 60)

    try:
        from datetime import datetime, timedelta

        from src.agent.external_info.tools import search_crypto_market_news

        # 计算日期范围
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)

        print("\n搜索查询: Bitcoin market news")
        print(f"日期范围: {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")

        # 调用工具
        results = search_crypto_market_news.invoke(
            {
                "query": "Bitcoin market news price analysis",
                "num_results": 3,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "exa_api_key": os.getenv("EXA_API_KEY"),
            }
        )

        print(f"\n✅ 搜索成功，返回 {len(results)} 条结果")
        for i, result in enumerate(results, 1):
            print(f"\n结果 {i}:")
            print(result[:200] + "..." if len(result) > 200 else result)

    except Exception as e:
        print(f"\n❌ 工具测试失败: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    # 检查必要的环境变量
    required_vars = ["OPENAI_API_KEY", "EXA_API_KEY"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        print(f"❌ 缺少必要的环境变量: {', '.join(missing_vars)}")
        print("请在 .env 文件中设置这些变量")
        sys.exit(1)

    # 运行测试
    try:
        # 测试工具
        test_tools_directly()

        # 测试 LangChain 工作流
        test_langchain_workflow()

        print("\n" + "=" * 60)
        print("✅ 所有测试完成")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\n⚠️ 测试被用户中断")
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
