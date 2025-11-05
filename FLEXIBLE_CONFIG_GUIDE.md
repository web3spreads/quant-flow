# 灵活配置和 Prompt 管理指南

## 🎯 新功能概述

本次更新实现了以下两个核心改进：

### 1. 灵活的交易配置
- **交易金额**：`max_trade_amount` 现在是**上限值**，AI 可以根据市场情况自主决定实际交易金额（1 到上限之间）
- **杠杆倍数**：`max_leverage` 现在是**最大值**，AI 可以根据风险评估自主选择 1 到最大值之间的任何杠杆

### 2. 可配置的 Prompt 系统
- Prompt 不再硬编码在代码中
- 支持创建多套 Prompt 策略
- 通过配置文件轻松切换不同的交易策略

---

## 📋 配置文件变更

### config.yaml 更新

#### 1. 交易配置部分

**旧版本:**
```yaml
trading:
  trade_amount: 100          # 固定金额
  default_leverage: 10       # 固定杠杆
```

**新版本:**
```yaml
trading:
  # 单笔交易金额上限（USD）
  # AI 可以根据市场情况自主决定实际交易金额，但不超过此上限
  max_trade_amount: 100

  # 最大杠杆倍数（1-50倍，具体取决于交易对）
  # AI 可以根据市场情况自主选择 1 到此上限之间的任何杠杆倍数
  max_leverage: 10
```

#### 2. 新增 Prompt 配置

```yaml
# Prompt 配置
prompt:
  # 使用的 Prompt 集合名称
  # 可选值: default, conservative, aggressive 或自定义名称
  # Prompt 集合在 prompts/prompts.yaml 中定义
  set: default

  # Prompt 配置文件路径
  config_file: prompts/prompts.yaml
```

---

## 📁 Prompt 目录结构

```
prompts/
├── prompts.yaml                      # Prompt 配置文件
└── default/                          # 默认策略 Prompt 集合
    ├── system_prompt.txt             # 系统角色定义
    ├── spot_system_prompt.txt        # 现货 Agent 角色定义
    ├── trading_prompt_template.txt   # 单币交易决策模板
    └── spot_prompt_template.txt      # 现货定投决策模板
```

---

## 🔧 AI 的新权限

### 交易金额决策

AI 现在可以根据信号强度自主决定交易金额：

**示例场景：**
- **强信号**（所有技术指标一致，多周期确认）：使用 80-100% 的上限金额
- **中等信号**（大部分条件满足）：使用 50-70% 的上限金额
- **弱信号**（部分条件满足）：使用 30-50% 的上限金额

**AI 调用示例：**
```python
# 强信号 - 使用接近上限的金额和杠杆
buy(symbol="BTC", amount=90, leverage=8)

# 中等信号 - 使用中等金额和杠杆
buy(symbol="BTC", amount=60, leverage=5)

# 弱信号 - 使用较小金额和杠杆
buy(symbol="BTC", amount=40, leverage=3)
```

### 杠杆倍数决策

AI 根据风险评估自主选择杠杆：

**决策逻辑：**
- **低风险环境**（明确趋势，强支撑位）：可使用较高杠杆（7-10x）
- **中等风险**（趋势形成中）：使用中等杠杆（4-6x）
- **高风险环境**（趋势不明朗）：使用低杠杆（1-3x）

---

## 🎨 创建自定义 Prompt 策略

### 步骤 1: 创建新的 Prompt 目录

```bash
mkdir -p prompts/my_strategy
```

### 步骤 2: 复制默认 Prompt

```bash
cp prompts/default/*.txt prompts/my_strategy/
```

### 步骤 3: 修改 Prompt 内容

根据您的交易策略调整 Prompt 内容。例如：

**保守策略** (`prompts/conservative/`)：
- 提高开仓条件（例如：RSI < 35 而不是 < 40）
- 降低杠杆建议（例如：最多使用 50% 最大杠杆）
- 强调风险控制

**激进策略** (`prompts/aggressive/`)：
- 放宽开仓条件
- 允许更高杠杆使用
- 更积极的仓位管理

### 步骤 4: 在 prompts.yaml 中注册新策略

编辑 `prompts/prompts.yaml`：

```yaml
prompt_sets:
  my_strategy:
    name: "我的自定义策略"
    description: "针对震荡市场优化的策略"
    system_prompt_file: "my_strategy/system_prompt.txt"
    spot_system_prompt_file: "my_strategy/spot_system_prompt.txt"
    trading_prompt_template_file: "my_strategy/trading_prompt_template.txt"
    spot_prompt_template_file: "my_strategy/spot_prompt_template.txt"
```

### 步骤 5: 切换到新策略

修改 `config.yaml`：

```yaml
prompt:
  set: my_strategy  # 从 default 改为 my_strategy
```

---

## 📊 策略 A/B 测试

### 测试流程

1. **创建多个 Prompt 策略**
   - `prompts/strategy_a/` - 策略 A
   - `prompts/strategy_b/` - 策略 B

2. **分别运行并记录数据**
   ```bash
   # 测试策略 A
   # 修改 config.yaml: prompt.set = strategy_a
   python main.py

   # 测试策略 B
   # 修改 config.yaml: prompt.set = strategy_b
   python main.py
   ```

3. **比较性能指标**
   - 总收益率
   - 胜率
   - 最大回撤
   - 夏普比率
   - 交易频率

4. **选择最优策略**

---

## 🛠️ 工具调用格式

### 买入开多 (buy)

**JSON 格式（推荐）：**
```json
{
  "symbol": "BTC",
  "amount": 60,      // 可选：交易金额
  "leverage": 5      // 可选：杠杆倍数
}
```

**简单格式（向后兼容）：**
```
"BTC"  # 使用默认上限金额和杠杆
```

### 卖空开空 (sell_short)

**JSON 格式：**
```json
{
  "symbol": "ETH",
  "amount": 40,
  "leverage": 3
}
```

### 现货买入 (buy_spot)

**JSON 格式：**
```json
{
  "symbol": "BTC",
  "amount": 80      // 可选：定投金额
}
```

---

## 🔍 调试和监控

### 查看当前使用的 Prompt 集合

程序启动时会显示：
```
✅ 已加载 Prompt 集合: 默认策略 - 平衡的交易策略，适合大多数市场环境
```

### 验证 Prompt 配置

```bash
python src/prompt_manager.py
```

输出示例：
```
当前 Prompt 集合: {'name': '默认策略', 'description': '平衡的交易策略', 'set_name': 'default'}

系统 Prompt 长度: 245 字符
现货系统 Prompt 长度: 312 字符
交易 Prompt 模板长度: 6543 字符
现货 Prompt 模板长度: 3456 字符
```

---

## ⚠️ 注意事项

### 配置兼容性

新配置**向后兼容**旧配置：

- 如果使用旧字段名 `trade_amount`，系统会自动识别为 `max_trade_amount`
- 如果使用旧字段名 `default_leverage`，系统会自动识别为 `max_leverage`
- 如果不配置 Prompt 选项，系统会使用默认 Prompt 集合

### AI 决策验证

系统会验证 AI 的参数选择：

- **金额验证**：AI 选择的金额不能超过配置的上限
- **杠杆验证**：AI 选择的杠杆不能超过配置的最大值
- **余额检查**：执行前会检查账户余额是否充足

### Prompt 文件格式

- 使用 UTF-8 编码
- 支持 Python f-string 格式化语法
- 模板变量使用 `{variable_name}` 格式

---

## 📚 示例：完整配置流程

### 场景：创建一个保守的定投策略

1. **创建 Prompt 文件**

```bash
mkdir -p prompts/conservative_dca
cp prompts/default/*.txt prompts/conservative_dca/
```

2. **修改交易 Prompt**（`prompts/conservative_dca/trading_prompt_template.txt`）

更严格的买入条件：
```
### 买入开多信号（需同时满足多个条件）:
1. RSI < 35（更深度超卖）          # 原来是 < 40
2. MACD 柱状图由负转正，或 MACD 线向上穿越信号线
3. 价格跌破 MA(99) 后反弹         # 更严格的条件
...

**金额和杠杆建议:**
- 即使强信号也只使用 50-60% 金额, 30-40% 最大杠杆  # 更保守
```

3. **注册策略**（`prompts/prompts.yaml`）

```yaml
prompt_sets:
  conservative_dca:
    name: "保守定投策略"
    description: "极度保守的策略，只在明确底部区域开仓"
    system_prompt_file: "conservative_dca/system_prompt.txt"
    spot_system_prompt_file: "conservative_dca/spot_system_prompt.txt"
    trading_prompt_template_file: "conservative_dca/trading_prompt_template.txt"
    spot_prompt_template_file: "conservative_dca/spot_prompt_template.txt"
```

4. **配置使用**（`config.yaml`）

```yaml
trading:
  max_trade_amount: 50      # 保守金额
  max_leverage: 5           # 保守杠杆

prompt:
  set: conservative_dca     # 使用保守策略
```

5. **启动程序**

```bash
python main.py
```

---

## 🎓 最佳实践

### 1. 渐进式调整

- 从默认策略开始
- 小幅度调整参数
- 观察一段时间后再进行大改动

### 2. 详细记录

- 为每个策略添加详细的 description
- 记录修改原因和预期效果
- 保存测试结果供后续分析

### 3. 风险控制

- 不要一次性修改太多参数
- 保持合理的上限值
- 定期检查 AI 的决策是否符合预期

### 4. 回测验证

- 在历史数据上测试新策略
- 对比不同策略的表现
- 选择稳定性好的策略

---

## 🆘 故障排除

### 问题 1：找不到 Prompt 文件

**错误信息：**
```
FileNotFoundError: Prompt 文件不存在: prompts/my_strategy/system_prompt.txt
```

**解决方案：**
- 检查文件路径是否正确
- 确认文件已创建
- 验证 prompts.yaml 中的配置

### 问题 2：AI 不使用自定义金额

**原因：**
- Prompt 中可能没有强调 AI 的自主决策权
- Agent 代码可能还未完全集成新功能

**解决方案：**
- 检查 Prompt 模板中是否包含金额和杠杆指导
- 确认 Agent 代码已更新

### 问题 3：参数验证失败

**错误信息：**
```
❌ 交易金额超过上限
```

**解决方案：**
- 检查 config.yaml 中的 max_trade_amount 设置
- 确认 AI 的决策是否合理
- 调整 Prompt 中的金额建议范围

---

## 📞 技术支持

如遇到问题，请：

1. 查看日志文件 `logs/` 目录
2. 检查配置文件格式是否正确
3. 验证 Prompt 文件是否存在
4. 提供完整的错误信息和配置文件内容

---

**更新日期：** 2025-11-05
**版本：** 1.0.0
**兼容性：** 向后兼容旧版配置
