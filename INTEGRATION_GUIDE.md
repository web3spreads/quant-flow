# 数据注入集成指南

本指南说明如何在main.py中集成新的数据增强功能,以支持nof1和nof1-improved prompts。

## 概述

我们添加了以下新功能:
1. **indicators.py**: 添加了EMA、ATR计算,以及历史序列数据获取
2. **data_enricher.py**: 新模块,负责收集所有额外的市场数据
3. **prompt_manager.py**: 扩展了`format_trading_prompt`方法,支持`enriched_data`参数

## 集成步骤

### 1. 在main.py中导入新模块

```python
from src.data.data_enricher import MarketDataEnricher
from datetime import datetime
```

### 2. 在TradingBot的__init__方法中初始化数据增强器

```python
class TradingBot:
    def __init__(self, config_file: str = "config.yaml"):
        # ... 现有代码 ...

        # 添加程序启动时间
        self.start_time = datetime.now()

        # 初始化数据增强器
        self.data_enricher = MarketDataEnricher(
            market_fetcher=self.market_fetcher,
            start_time=self.start_time
        )
```

### 3. 在run_strategy方法中获取并使用enriched_data

在调用`agent.make_decision()`之前,添加以下代码:

```python
# 在获取15分钟数据后,获取4小时数据
df_4h = self.market_fetcher.fetch_ohlcv(
    symbol,
    timeframe="4h",
    limit=100
)

if df_4h is not None:
    df_4h = TechnicalIndicators.calculate_all_indicators(
        df_4h,
        ema_periods=[20, 50],  # 计算EMA20和EMA50
        atr_periods=[3, 14]     # 计算ATR3和ATR14
    )

# 使用数据增强器获取额外数据
enriched_data = self.data_enricher.enrich_market_data(
    symbol=symbol,
    market_data=market_data,
    df_15m=df,  # 15分钟数据
    df_4h=df_4h  # 4小时数据
)

# 获取增强的账户数据
account_enriched = self.data_enricher.enrich_account_data(
    balance_info=balance_dict,
    initial_balance=self.config.initial_balance  # 需要在config中添加
)

# 合并account数据到enriched_data
enriched_data.update(account_enriched)
```

### 4. 修改agent.make_decision调用

在`src/agent/single_symbol_agent.py`的`make_decision`方法中,
需要将`enriched_data`传递给`prompt_manager.format_trading_prompt`:

```python
# 在single_symbol_agent.py的make_decision方法中:
def make_decision(
    self,
    market_data: Dict[str, Any],
    multi_timeframe_trends: Dict[str, str],
    current_positions: list,
    max_positions: int,
    historical_summary: Optional[str] = None,
    enriched_data: Optional[Dict[str, Any]] = None  # 添加此参数
) -> Tuple[str, Dict[str, Any]]:
    # ... 现有代码 ...

    prompt = self.prompt_manager.format_trading_prompt(
        symbol=self.symbol,
        market_data=market_data,
        multi_timeframe_trends=multi_timeframe_trends,
        current_positions=current_positions,
        max_positions=max_positions,
        max_trade_amount=self.trade_amount,
        max_leverage=self.max_leverage,
        take_profit_ratio=self.take_profit_ratio,
        stop_loss_ratio=self.stop_loss_ratio,
        historical_summary=historical_summary,
        balance_info=balance_dict,
        enriched_data=enriched_data  # 添加此参数
    )
```

### 5. 在main.py中传递enriched_data

```python
# 在run_strategy中调用agent.make_decision时:
decision, details = agent.make_decision(
    market_data=market_data,
    multi_timeframe_trends=multi_timeframe_trends,
    current_positions=current_positions,
    max_positions=self.config.max_positions,
    historical_summary=historical_summary,
    enriched_data=enriched_data  # 添加此参数
)
```

## 配置更新

在`config.yaml`中添加初始余额配置(用于计算回报率):

```yaml
trading:
  initial_balance: 10000.0  # 初始资金,用于计算total_return_pct
```

## 向后兼容性

所有改动都是向后兼容的:
- 如果不提供`enriched_data`,系统会使用默认值
- 现有的default、conservative、aggressive prompts不需要这些额外数据,可以正常工作
- 只有nof1和nof1-improved prompts才会使用这些额外字段

## 测试

切换到nof1或nof1-improved prompt集进行测试:

```yaml
# 在prompts/prompts.yaml中:
active_set: nof1  # 或 nof1-improved
```

然后运行程序,检查prompt中是否包含所有额外的数据字段。

## 注意事项

1. **持仓量(Open Interest)**: 目前使用占位符值0,需要Hyperliquid API支持才能获取实际数据
2. **夏普比率**: 需要历史收益数据才能准确计算,当前返回0
3. **性能影响**: 获取4小时数据会增加API调用,但对整体性能影响很小

## 完整示例

参考以下伪代码了解完整流程:

```python
# main.py的run_strategy方法片段
for symbol in self.config.trading_symbols:
    # 1. 获取15分钟数据
    df = self.market_fetcher.fetch_ohlcv(symbol, '15m', 100)
    df = TechnicalIndicators.calculate_all_indicators(df)
    market_data = TechnicalIndicators.get_latest_indicators(df)

    # 2. 获取4小时数据
    df_4h = self.market_fetcher.fetch_ohlcv(symbol, '4h', 100)
    df_4h = TechnicalIndicators.calculate_all_indicators(
        df_4h,
        ema_periods=[20, 50],
        atr_periods=[3, 14]
    )

    # 3. 增强数据
    enriched_data = self.data_enricher.enrich_market_data(
        symbol, market_data, df, df_4h
    )
    enriched_data.update(
        self.data_enricher.enrich_account_data(balance_dict, 10000)
    )

    # 4. 调用agent
    decision, details = agent.make_decision(
        market_data=market_data,
        multi_timeframe_trends=multi_timeframe_trends,
        current_positions=current_positions,
        max_positions=self.config.max_positions,
        historical_summary=historical_summary,
        enriched_data=enriched_data
    )
```

## 支持

如有问题,请检查:
1. `src/data/data_enricher.py` - 数据增强实现
2. `src/data/indicators.py` - 新增的ATR、EMA计算
3. `src/prompt_manager.py` - enriched_data集成
