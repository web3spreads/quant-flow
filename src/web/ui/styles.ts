/**
 * 看板样式表（内联，零外部资源）。
 *
 * 三条视觉规则，全部为「多账户不串台」服务：
 * ① 每个账户有稳定的身份色（--ac），出现在侧栏色条、作用域头、图表线与表格行首；
 * ② 环境（主网/测试网）优先级高于身份色——主网一律走 .live 警示态（红色轨条 +
 *    斜纹底 + 「实盘」字样），测试网走 .sim 静默态；
 * ③ 盈亏色（--up/--down）只表示数字正负，绝不参与账户身份，避免与身份色混淆。
 */

export const STYLES = String.raw`
:root{
  --bg:#0b0f1a;--panel:#141b2b;--panel2:#1b2436;--panel3:#212b40;
  --line:#26314a;--line2:#334159;
  --text:#e2e8f5;--dim:#8391b0;--dim2:#5f6d8c;
  --acc:#4d6bfe;--up:#2ebd85;--down:#f6465d;--warn:#f0b90b;
  --live:#f6465d;--sim:#38bdf8;
  --ac:#4d6bfe;              /* 当前作用域的账户身份色，由 JS 覆写 */
  --sidebar:236px;
  --r:12px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);
  font:13px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--acc)}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}.dim2{color:var(--dim2)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}

/* ── 顶栏 ───────────────────────────────────────────────────────── */
.topbar{display:flex;align-items:center;gap:12px;height:52px;padding:0 16px;
  background:linear-gradient(180deg,#151d2f,#111828);border-bottom:1px solid var(--line);
  position:sticky;top:0;z-index:20}
.logo{display:flex;align-items:center;gap:9px;font-weight:700;font-size:15px;letter-spacing:.2px}
.logo .mark{width:22px;height:22px;border-radius:7px;background:linear-gradient(135deg,var(--acc),#7c5cff);
  display:grid;place-items:center;font-size:12px;color:#fff}
.logo .sub{font-weight:400;font-size:11px;color:var(--dim2)}
.grow{flex:1}
.ctl{display:flex;align-items:center;gap:6px}
.ctl select,.ctl input{background:var(--panel2);border:1px solid var(--line);color:var(--text);
  border-radius:8px;padding:4px 8px;font-size:12px}
.iconbtn{background:var(--panel2);border:1px solid var(--line);color:var(--dim);border-radius:8px;
  padding:4px 10px;cursor:pointer;font-size:12px}
.iconbtn:hover{color:var(--text);border-color:var(--line2)}
.iconbtn.on{color:var(--up);border-color:var(--up)}
.pulse{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--up);
  margin-right:6px;box-shadow:0 0 0 0 rgba(46,189,133,.6);animation:p 2.4s infinite}
.pulse.off{background:var(--dim2);animation:none}
@keyframes p{0%{box-shadow:0 0 0 0 rgba(46,189,133,.5)}70%{box-shadow:0 0 0 7px rgba(46,189,133,0)}100%{box-shadow:0 0 0 0 rgba(46,189,133,0)}}

/* ── 骨架 ───────────────────────────────────────────────────────── */
.app{display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr);min-height:calc(100vh - 52px)}
.side{background:#0e1422;border-right:1px solid var(--line);padding:10px 8px 24px;
  position:sticky;top:52px;height:calc(100vh - 52px);overflow-y:auto}
.side::-webkit-scrollbar,main::-webkit-scrollbar,.panel::-webkit-scrollbar{width:8px;height:8px}
.side::-webkit-scrollbar-thumb,main::-webkit-scrollbar-thumb,.panel::-webkit-scrollbar-thumb{background:var(--line);border-radius:4px}
.side h4{margin:14px 8px 6px;font-size:10px;letter-spacing:1.4px;color:var(--dim2);text-transform:uppercase}
main{padding:0 0 40px;min-width:0}
.wrap{padding:16px 20px;max-width:1560px;margin:0 auto}

/* ── 侧栏条目 ───────────────────────────────────────────────────── */
.navitem{display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:10px;
  cursor:pointer;color:var(--dim);border:1px solid transparent;margin-bottom:2px}
.navitem:hover{background:var(--panel);color:var(--text)}
.navitem.active{background:var(--panel2);color:var(--text);border-color:var(--line)}
.navitem .ico{width:18px;text-align:center;opacity:.85}

.acard{position:relative;display:block;width:100%;text-align:left;padding:9px 10px 9px 14px;margin-bottom:6px;
  border-radius:10px;background:var(--panel);border:1px solid var(--line);cursor:pointer;overflow:hidden}
.acard:hover{border-color:var(--line2);background:var(--panel2)}
.acard.active{border-color:var(--ac);background:linear-gradient(90deg,rgba(255,255,255,.05),transparent)}
.acard .rail{position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--ac)}
.acard.live .rail{background:repeating-linear-gradient(135deg,var(--live) 0 4px,#7d1220 4px 8px)}
.acard .top{display:flex;align-items:center;gap:6px}
.acard .nm{font-weight:600;font-size:13px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.acard .mid{display:flex;justify-content:space-between;align-items:baseline;margin-top:3px}
.acard .eq{font-size:14px;font-weight:600}
.acard .pl{font-size:11px}
.acard .foot{display:flex;align-items:center;gap:6px;margin-top:4px;height:22px}
.acard .spark{flex:1;height:22px}

/* ── 徽标 ───────────────────────────────────────────────────────── */
.badge{display:inline-flex;align-items:center;gap:4px;padding:1px 7px;border-radius:6px;font-size:11px;
  background:var(--panel2);border:1px solid var(--line);color:var(--dim);white-space:nowrap}
.badge.ok{color:var(--up);border-color:rgba(46,189,133,.45);background:rgba(46,189,133,.1)}
.badge.bad{color:var(--down);border-color:rgba(246,70,93,.45);background:rgba(246,70,93,.1)}
.badge.warn{color:var(--warn);border-color:rgba(240,185,11,.4);background:rgba(240,185,11,.1)}
.badge.live{color:#fff;background:var(--live);border-color:var(--live);font-weight:600;letter-spacing:.3px}
.badge.sim{color:var(--sim);border-color:rgba(56,189,248,.45);background:rgba(56,189,248,.1)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ac);flex:none}
.avatar{width:26px;height:26px;border-radius:8px;flex:none;display:grid;place-items:center;
  font-size:12px;font-weight:700;color:#0b0f1a;background:var(--ac)}
.avatar.sm{width:18px;height:18px;border-radius:6px;font-size:10px}
.tag{display:inline-block;padding:0 7px;border-radius:6px;font-size:11px;background:var(--panel3);
  border:1px solid var(--line);color:var(--dim)}
.tag.grid{color:#a78bfa;border-color:rgba(167,139,250,.4)}

/* ── 作用域头（多账户防串台的核心）────────────────────────────── */
.scope{position:relative;border:1px solid var(--line);border-left:5px solid var(--ac);
  border-radius:var(--r);background:linear-gradient(90deg,rgba(255,255,255,.045),transparent 60%),var(--panel);
  padding:12px 16px;margin-bottom:14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.scope.live{border-left-color:var(--live);
  background:repeating-linear-gradient(135deg,rgba(246,70,93,.09) 0 10px,transparent 10px 20px),var(--panel)}
.scope .id{display:flex;align-items:center;gap:10px}
.scope .nm{font-size:18px;font-weight:700;letter-spacing:.2px}
.scope .meta{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:12px}
.scope .meta b{color:var(--text);font-weight:500}
.scope .kpi{display:flex;gap:18px;margin-left:auto;text-align:right}
.scope .kpi .k{font-size:11px;color:var(--dim)}
.scope .kpi .v{font-size:17px;font-weight:600}
.liverail{position:fixed;top:0;left:0;right:0;height:3px;z-index:40;
  background:repeating-linear-gradient(90deg,var(--live) 0 14px,#5c0f19 14px 28px)}
body.livemode .topbar{border-bottom-color:rgba(246,70,93,.55)}
body.livemode .side{border-right-color:rgba(246,70,93,.28)}

/* ── 标签页 ─────────────────────────────────────────────────────── */
.tabs{display:flex;gap:2px;margin-bottom:14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.tabs button{background:none;border:none;border-bottom:2px solid transparent;color:var(--dim);
  padding:8px 14px;cursor:pointer;font-size:13px;margin-bottom:-1px}
.tabs button:hover{color:var(--text)}
.tabs button.active{color:var(--text);border-bottom-color:var(--ac)}

/* ── 卡片与面板 ─────────────────────────────────────────────────── */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:10px;margin-bottom:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:11px 14px;position:relative;overflow:hidden}
.card .k{color:var(--dim);font-size:11px;display:flex;align-items:center;gap:5px}
.card .v{font-size:21px;font-weight:600;margin-top:1px;letter-spacing:-.3px}
.card .s{font-size:11px;color:var(--dim2);margin-top:1px}
.card.hl{border-color:var(--ac)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:13px 15px;margin-bottom:14px;min-width:0}
.panel>h3{margin:0 0 10px;font-size:13px;font-weight:600;color:var(--text);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.panel>h3 .hint{font-weight:400;font-size:11px;color:var(--dim)}
.panel .scroll{overflow-x:auto}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.g21{display:grid;grid-template-columns:2fr 1fr;gap:14px}
@media(max-width:1180px){.g2,.g21{grid-template-columns:1fr}}
@media(max-width:1000px){
  .app{grid-template-columns:1fr}
  .side{position:static;height:auto;display:flex;gap:8px;overflow-x:auto;border-right:none;border-bottom:1px solid var(--line);padding:8px}
  .side h4{display:none}.side .navwrap{display:flex;gap:6px}
  .acard{min-width:190px;margin-bottom:0}
  .wrap{padding:12px}
}

/* ── 表格 ───────────────────────────────────────────────────────── */
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{color:var(--dim);text-align:left;font-weight:500;padding:7px 9px;border-bottom:1px solid var(--line);
  white-space:nowrap;position:sticky;top:0;background:var(--panel);z-index:1}
th.s{cursor:pointer;user-select:none}th.s:hover{color:var(--text)}
td{padding:7px 9px;border-bottom:1px solid rgba(38,49,74,.55);vertical-align:middle}
tbody tr:hover td{background:rgba(255,255,255,.028)}
tr.click{cursor:pointer}
td.r,th.r{text-align:right}
.rowid{display:flex;align-items:center;gap:7px}
.rowid .bar{width:3px;height:22px;border-radius:2px;background:var(--ac);flex:none}

pre{background:#0d1320;border:1px solid var(--line);border-radius:8px;padding:9px 11px;white-space:pre-wrap;
  word-break:break-all;font-size:11.5px;max-height:300px;overflow:auto;color:#b9c5dd;margin:6px 0 0}
details summary{cursor:pointer;color:var(--dim);font-size:12px}
details summary:hover{color:var(--text)}
.empty{color:var(--dim2);text-align:center;padding:26px 0;font-size:12.5px}

/* ── 图表 ───────────────────────────────────────────────────────── */
.chart{width:100%;display:block;overflow:visible}
.chart text{fill:var(--dim);font-size:11px;font-family:inherit}
.chart .axis{stroke:var(--line);stroke-width:1}
.chart .gridline{stroke:var(--line);stroke-width:1;stroke-dasharray:2 4;opacity:.55}
.chart .hb{fill:transparent}
.chart .hx{stroke:var(--dim);stroke-width:1;stroke-dasharray:3 3;opacity:0}
.chart .hd{opacity:0}
.chart .hbg:hover .hx,.chart .hbg:hover .hd{opacity:1}
.chart .hbg:hover .hb{fill:rgba(255,255,255,.04)}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:6px;font-size:11.5px;color:var(--dim)}
.legend .li{display:flex;align-items:center;gap:5px;cursor:default}
.legend .sw{width:9px;height:3px;border-radius:2px}
.chartbar{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{background:none;border:none;color:var(--dim);padding:3px 10px;cursor:pointer;font-size:11.5px}
.seg button.active{background:var(--panel3);color:var(--text)}

#tip{position:fixed;z-index:60;pointer-events:none;display:none;background:rgba(13,19,32,.97);
  border:1px solid var(--line2);border-radius:8px;padding:7px 10px;font-size:11.5px;line-height:1.5;
  box-shadow:0 8px 26px rgba(0,0,0,.5);max-width:280px}
#tip .t{color:var(--dim);margin-bottom:3px}
#tip .r{display:flex;justify-content:space-between;gap:12px}
#tip .r .sw{width:8px;height:8px;border-radius:2px;display:inline-block;margin-right:5px}

/* ── 表单 ───────────────────────────────────────────────────────── */
.field{display:grid;grid-template-columns:250px minmax(180px,260px) 1fr;gap:12px;align-items:center;
  padding:7px 4px;border-bottom:1px solid rgba(38,49,74,.45)}
.field:last-child{border-bottom:none}
.field label{font-size:12.5px;font-family:ui-monospace,Menlo,Consolas,monospace}
.field .desc{color:var(--dim);font-size:11.5px}
.field input[type=text],.field input[type=number],.field select,.field textarea{width:100%;
  background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:8px;padding:5px 9px;font-size:12.5px}
.field textarea{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px}
.field.override label::after{content:" ●";color:var(--warn)}
@media(max-width:900px){.field{grid-template-columns:1fr}}
button.btn{background:var(--acc);color:#fff;border:none;padding:7px 16px;border-radius:8px;cursor:pointer;font-size:13px}
button.btn.ghost{background:var(--panel2);border:1px solid var(--line);color:var(--text)}
button.btn.danger{background:var(--down)}
button.btn:disabled{opacity:.5;cursor:not-allowed}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
#toast{position:fixed;right:18px;bottom:18px;background:var(--panel2);border:1px solid var(--line2);
  border-radius:10px;padding:10px 15px;display:none;max-width:440px;z-index:70;box-shadow:0 10px 30px rgba(0,0,0,.5)}
`;
