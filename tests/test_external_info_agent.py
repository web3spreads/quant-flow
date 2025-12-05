"""
外部信息收集 Agent 测试
测试 LangChain 工作流集成
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.agent.external_info_agent import ExternalInfoAgent
from src.agent.market_info_store import TimePeriod
from src.utils.logger import get_logger


def test_langchain_workflow():
    """测试 LangChain 工作流模式"""
    print("=" * 60)
    print("测试 LangChain 工作流模式")
    print("=" * 60)
    
    logger = get_logger()
    
    # 创建 Agent（使用 LangChain 工作流）
    agent = ExternalInfoAgent(
        logger=logger,
        openai_api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4"),
        exa_api_key=os.getenv("EXA_API_KEY"),
        symbols=["BTC", "ETH"],
        use_langchain_workflow=True  # 使用新的工作流
    )
    
    # 测试收集单个周期
    print("\n测试收集 daily 周期...")
    saved_files = agent.collect_and_save(periods=[TimePeriod.DAILY])
    
    if saved_files:
        print(f"\n✅ 成功生成 {len(saved_files)} 份报告:")
        for period, file_path in saved_files.items():
            print(f"  - {period}: {file_path}")
    else:
        print("\n❌ 未生成任何报告")
    
    # 获取报告状态
    print("\n报告状态:")
    status = agent.get_report_status()
    for period, info in status.items():
        print(f"  {period}: {info['total_files']} 个文件")


def test_traditional_mode():
    """测试传统模式"""
    print("\n" + "=" * 60)
    print("测试传统模式（exa_py）")
    print("=" * 60)
    
    logger = get_logger()
    
    # 创建 Agent（使用传统模式）
    agent = ExternalInfoAgent(
        logger=logger,
        openai_api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4"),
        exa_api_key=os.getenv("EXA_API_KEY"),
        symbols=["BTC"],
        use_langchain_workflow=False  # 使用传统模式
    )
    
    print("\n测试收集 daily 周期...")
    saved_files = agent.collect_and_save(periods=[TimePeriod.DAILY])
    
    if saved_files:
        print(f"\n✅ 成功生成 {len(saved_files)} 份报告:")
        for period, file_path in saved_files.items():
            print(f"  - {period}: {file_path}")
    else:
        print("\n❌ 未生成任何报告")


def test_tools_directly():
    """直接测试工具"""
    print("\n" + "=" * 60)
    print("直接测试 LangChain 工具")
    print("=" * 60)
    
    try:
        from src.agent.external_info.tools import search_crypto_market_news
        from datetime import datetime, timedelta
        
        # 计算日期范围
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)
        
        print(f"\n搜索查询: Bitcoin market news")
        print(f"日期范围: {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")
        
        # 调用工具
        results = search_crypto_market_news.invoke({
            "query": "Bitcoin market news price analysis",
            "num_results": 3,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d")
        })
        
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
        
        # 测试传统模式
        # test_traditional_mode()
        
        print("\n" + "=" * 60)
        print("✅ 所有测试完成")
        print("=" * 60)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 测试被用户中断")
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
