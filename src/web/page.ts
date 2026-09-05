/**
 * 看板单页应用（内联于插件，零外部资源、零依赖）。
 *
 * 页面由 ui/ 下的几段源码拼装：样式 → 图表工具 → 外壳 → 各作用域视图。
 * 拆开是为了可读与可测——视图层全是纯函数（数据进、HTML 字符串出），
 * tests/webUi.test.ts 在 node:vm 里直接对它们断言，不需要浏览器。
 *
 * 导航是「作用域 + 页签」两级：大盘 / 每个账户 / 全局（回测·配置）。
 * 账户身份色、环境徽标、实盘警示轨条三处冗余表达「你正在看哪个账户」。
 */

import { STYLES } from "./ui/styles.js";
import { CHARTS_JS } from "./ui/charts.js";
import { SHELL_JS } from "./ui/shell.js";
import { VIEWS_FLEET_JS } from "./ui/views-fleet.js";
import { VIEWS_ACCOUNT_JS } from "./ui/views-account.js";
import { VIEWS_GLOBAL_JS } from "./ui/views-global.js";

const BODY = String.raw`
<div id="liverail" class="liverail" style="display:none"></div>
<div class="topbar">
  <div class="logo"><span class="mark">Q</span>Quant Flow<span class="sub">dsh-plugin</span></div>
  <span class="grow"></span>
  <div class="ctl">
    <span id="refreshctl"></span>
    <button class="iconbtn" onclick="render()" title="立即刷新">⟳ 刷新</button>
    <span class="dim" id="ts" style="font-size:11.5px;min-width:120px;text-align:right"></span>
  </div>
</div>
<div class="app">
  <aside class="side" id="side"></aside>
  <main><div class="wrap" id="view"></div></main>
</div>
<div id="tip"></div>
<div id="toast"></div>
`;

export const DASHBOARD_HTML = [
  '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">',
  '<meta name="viewport" content="width=device-width, initial-scale=1">',
  "<title>Quant Flow 看板</title>",
  "<style>",
  STYLES,
  "</style>\n</head>\n<body>",
  BODY,
  "<script>",
  SHELL_JS,
  CHARTS_JS,
  VIEWS_FLEET_JS,
  VIEWS_ACCOUNT_JS,
  VIEWS_GLOBAL_JS,
  // 测试沙箱只加载函数定义，不启动轮询与取数
  "if(!globalThis.__QF_TEST__)boot();",
  "</scr" + "ipt>\n</body>\n</html>",
].join("\n");
