---
sidebar_position: 1
title: 环境变量
description: 完整的 .env 配置变量参考
---

# 环境变量

所有密钥和环境特定配置都存储在 `.env` 文件中。该文件已被 `.gitignore` 排除在版本控制之外。

```bash
cp .env.example .env
```

## LLM 提供商密钥

至少需要一个 LLM API 密钥。使用哪个密钥取决于 `config.yaml` 中的 `client_type`。

| 变量 | 是否必须 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | 使用 OpenAI 时必须 | OpenAI API 密钥（`sk-...`） |
| `CLOUDFLARE_ACCOUNT_ID` | 使用 Cloudflare 时必须 | Cloudflare 账户 ID |
| `CLOUDFLARE_API_TOKEN` | 使用 Cloudflare 时必须 | Cloudflare Workers AI Token |
| `GOOGLE_API_KEY` | 使用 Gemini 时必须 | Google AI Studio API 密钥 |
| `NVIDIA_API_KEY` | 使用 NVIDIA NIM 时必须 | NVIDIA NIM API 密钥（`nvapi-...`） |

:::info LiteLLM 代理
如果使用 `client_type: litellm`，请设置 `OPENAI_API_KEY` 或在 `config.yaml` 中配置 LiteLLM 代理 URL。
:::

## Hyperliquid 凭据

| 变量 | 是否必须 | 说明 |
|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | **必须** | 用于签署订单的以太坊私钥（`0x...`） |
| `HYPERLIQUID_TESTNET` | 否 | `true` 使用测试网，`false` 使用主网（默认：`false`） |

:::danger 私钥安全
- 永远不要将 `.env` 提交到任何仓库
- 永远不要与任何人分享你的私钥
- 私钥丢失意味着资产永久丢失
- 建议使用专用的交易钱包，不要使用持有主要资产的钱包
:::

### API 钱包授权

Hyperliquid 支持使用 API 钱包（子钱包）进行程序化交易。如果你使用 API 钱包地址：

1. API 钱包可以查询余额，但**在未授权前无法交易**
2. 用你的**主钱包**打开 Hyperliquid 网页端
3. 进入**设置 → API 钱包**
4. 授权 API 钱包地址

:::tip
使用 API 钱包比直接使用主钱包私钥更安全——你可以随时通过网页界面撤销 API 钱包的访问权限。
:::

## 可选：外部数据 API

这些 API 被 [CEX 信号与链上数据](../features/cex-signals.md) 功能使用。全部可选——如不可用，系统会自动降级处理。

| 变量 | 说明 |
|---|---|
| `EXA_API_KEY` | [Exa](https://exa.ai) API 密钥，用于 ExternalInfoAgent（市场资讯） |

:::info 自动降级
如果外部 API 调用失败（网络错误、频率限制、密钥缺失），系统会记录警告并在没有该数据源的情况下继续运行。交易不会被阻断。
:::

## `.env` 示例

```bash
# ── LLM ────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxx

# ── Hyperliquid ────────────────────────────────────────────────────
HYPERLIQUID_PRIVATE_KEY=0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890
HYPERLIQUID_TESTNET=true

# ── 可选外部数据 ───────────────────────────────────────────────────
EXA_API_KEY=exa-xxxxxxxxxxxxxxxxxxxx
```

## 下一步

- [config.yaml 参考](./config-yaml.md) — 交易参数和功能开关
- [Docker 部署](../getting-started/docker.md) — 通过 Docker Compose 传入 `.env`
