# Coin-Specific Customizable Prompts

## Overview

The QuantFlow bot now supports **coin-specific customizable prompts** using the Jinja2 template engine. This allows you to create different trading strategies and behaviors for different cryptocurrencies (BTC, ETH, altcoins, etc.).

## Features

### 1. Conditional Logic Based on Coin Type

You can now use conditional statements in your prompt templates to customize behavior for different coins:

```jinja2
{% if is_BTC %}
## 💎 BTC Special Strategy
- Can hold for the long term
- Increase DCA during deep pullbacks
{% elif is_ETH %}
## 🔷 ETH Special Strategy
- Add to positions below $2500
- Focus on network upgrades
{% else %}
## ⚠️ Altcoin Strategy
- Prioritize closing positions
- Use lower leverage
{% endif %}
```

### 2. Available Variables

All prompt templates have access to the following coin-specific variables:

#### Coin Type Booleans
- `is_BTC` - True if trading BTC
- `is_ETH` - True if trading ETH
- `is_SOL` - True if trading SOL
- `is_major_coin` - True if coin is BTC or ETH
- `is_altcoin` - True if coin is NOT BTC or ETH

#### Market Data (formatted and raw)
- `symbol` / `coin` - The trading pair (e.g., "BTC", "ETH")
- `current_price` / `current_price_raw` - Current price (formatted string / raw float)
- `rsi` / `rsi_raw` - RSI indicator
- `macd` / `macd_raw` - MACD value
- `macd_signal` / `macd_signal_raw` - MACD signal line
- `macd_hist` / `macd_hist_raw` - MACD histogram
- `ma_7` / `ma_7_raw` - 7-period moving average
- `ma_25` / `ma_25_raw` - 25-period moving average
- `ma_99` / `ma_99_raw` - 99-period moving average
- `bb_upper` / `bb_upper_raw` - Bollinger Band upper
- `bb_middle` / `bb_middle_raw` - Bollinger Band middle
- `bb_lower` / `bb_lower_raw` - Bollinger Band lower
- `bb_position` / `bb_position_raw` - Price position in Bollinger Bands
- `volume_change` / `volume_change_raw` - Volume change percentage
- `multi_timeframe_trends` - Multi-timeframe trend analysis text

#### Position Information
- `position_count` - Current number of positions
- `max_positions` - Maximum allowed positions
- `has_long` / `has_long_bool` - Long position status (formatted / boolean)
- `has_short` / `has_short_bool` - Short position status (formatted / boolean)

#### Trading Parameters
- `max_trade_amount` / `max_trade_amount_raw` - Maximum trade amount
- `max_leverage` - Maximum leverage
- `take_profit_ratio` / `take_profit_ratio_raw` - Take profit ratio
- `stop_loss_ratio` / `stop_loss_ratio_raw` - Stop loss ratio

#### Fee Calculations
- `position_value` / `position_value_raw` - Position value
- `open_fee` / `open_fee_raw` - Opening fee
- `close_fee` / `close_fee_raw` - Closing fee
- `total_fee` / `total_fee_raw` - Total fees
- `breakeven_percent` - Breakeven percentage
- `price_move_percent` - Required price movement percentage

#### Other
- `historical_summary` - Historical decision summary
- `balance_info` - Account balance information

**Note:** Variables ending in `_raw` are numeric values, while those without are pre-formatted strings.

## Examples

### Example 1: Different Leverage for Different Coins

```jinja2
## 💰 Trading Parameters

{% if is_BTC %}
- Recommended leverage: Use up to {{ max_leverage }}x for strong signals
- BTC is relatively stable, suitable for higher leverage
{% elif is_ETH %}
- Recommended leverage: Use up to {{ max_leverage * 0.8 }}x
- ETH has good liquidity but higher volatility than BTC
{% else %}
- Recommended leverage: Maximum 3x recommended for altcoins
- High volatility requires strict risk management
{% endif %}
```

### Example 2: Different Entry Conditions

```jinja2
## 📖 Entry Conditions

{% if is_major_coin %}
**Major Coin Strategy (BTC/ETH):**
- Can enter with RSI < 40
- Suitable for longer holding periods
- Consider DCA on deep pullbacks (RSI < 30)
{% else %}
**Altcoin Strategy:**
- Only enter with RSI < 30 (deep oversold)
- Quick in and out, don't hold long
- Strict stop-loss at -{{ stop_loss_ratio }}
{% endif %}
```

### Example 3: Coin-Specific Price Alerts

```jinja2
{% if is_ETH and current_price_raw < 2500 %}
## 🔔 Special Alert
ETH is currently below $2,500 - this may be a good accumulation zone according to your strategy.
Consider increasing position size if other indicators align.
{% endif %}
```

### Example 4: Dynamic Risk Management

```jinja2
{% if is_altcoin %}
## ⚠️ Altcoin Risk Warning
You are trading {{ symbol }}, a non-major coin.
- Maximum position size: {{ max_trade_amount_raw * 0.5 }} USD (50% of normal)
- Recommended leverage: 1-3x only
- Exit quickly if RSI > 70
{% endif %}
```

## How to Customize

### Step 1: Choose Your Prompt Set

Prompts are organized by strategy in the `/prompts` directory:
- `/prompts/default/` - Balanced strategy
- `/prompts/conservative/` - Low risk, conservative strategy
- `/prompts/aggressive/` - High risk, aggressive strategy

### Step 2: Edit the Template Files

Each strategy has 4 template files:
- `system_prompt.txt` - System role definition (plain text)
- `spot_system_prompt.txt` - Spot trading role (plain text)
- `trading_prompt_template.txt` - Trading decision template (Jinja2)
- `spot_prompt_template.txt` - Spot trading template (Jinja2)

### Step 3: Use Jinja2 Syntax

The template files support full Jinja2 syntax:

#### Conditionals
```jinja2
{% if condition %}
  ...
{% elif another_condition %}
  ...
{% else %}
  ...
{% endif %}
```

#### Variables
```jinja2
{{ variable_name }}
```

#### Filters
```jinja2
{{ current_price_raw | round(2) }}
{{ max_leverage * 0.5 }}
```

#### Comments
```jinja2
{# This is a comment and won't appear in the output #}
```

### Step 4: Test Your Changes

After modifying templates, restart the bot to load the new prompts. The bot will automatically validate and render the templates using Jinja2.

## Best Practices

1. **Test Thoroughly**: Always test your conditional logic with different coins before deploying
2. **Use Raw Values for Calculations**: Use `_raw` variables when doing math operations
3. **Keep It Readable**: Don't overcomplicate templates - clarity is important
4. **Document Your Changes**: Add comments to explain complex conditional logic
5. **Backward Compatibility**: Ensure your templates work for all supported coins

## Migration from Old Format

Old format (Python .format()):
```python
你是一位交易专家，专注于 {symbol} 的交易决策。
当前价格: ${current_price}
```

New format (Jinja2):
```jinja2
你是一位交易专家，专注于 {{ symbol }} 的交易决策。
当前价格: ${{ current_price }}
```

The system is backward compatible - simple variable substitutions work the same way, you just need to change `{var}` to `{{ var }}`.

## Troubleshooting

### Template Rendering Errors

If you see template rendering errors:
1. Check for unmatched `{% %}` or `{{ }}` brackets
2. Verify variable names match the available variables list
3. Look for syntax errors in conditional statements
4. Check the logs for specific error messages

### Variables Not Working

If variables aren't being replaced:
1. Ensure you're using `{{ variable }}` not `{variable}`
2. Check that the variable name is correct (case-sensitive)
3. Verify the variable is available in the template context (see list above)

## Advanced Usage

### Creating Custom Coin-Specific Strategies

You can create completely different trading strategies for different coins by using complex conditionals:

```jinja2
{% if is_BTC %}
  {% include 'btc_specific_rules.txt' %}
{% elif is_ETH %}
  {% include 'eth_specific_rules.txt' %}
{% else %}
  {% include 'altcoin_rules.txt' %}
{% endif %}
```

Note: The `{% include %}` directive requires the included files to be in the prompts directory.

## Contributing

When creating new coin-specific strategies, please:
1. Test with multiple coins (BTC, ETH, and at least one altcoin)
2. Document your conditional logic
3. Share successful strategies with the community

## Support

For questions or issues with coin-specific prompts:
- Check the documentation in `/docs`
- Review example templates in `/prompts/default`, `/prompts/conservative`, `/prompts/aggressive`
- Open an issue on GitHub with template examples and error messages
