/**
 * 看板外壳：全局状态、取数、侧栏（账户导航）、作用域头、标签页、刷新节拍与浮层。
 *
 * 导航模型是「作用域 + 页签」两级，而不是一排平铺的页签：
 *   作用域 = 大盘 | 某个账户 | 全局（回测/配置）
 * 账户作用域下的每一次取数都自动带 ?account=<name>，页面所有色彩、标题、
 * 浏览器标签名都跟着切——**看错账户比看不到数据更危险**，所以身份信息宁可重复三遍。
 */

export const SHELL_JS = String.raw`
"use strict";
var ACOLORS=["#4d6bfe","#a78bfa","#22d3ee","#f0b90b","#fb923c","#f472b6",
             "#a3e635","#38bdf8","#c084fc","#2dd4bf","#818cf8","#94a3b8"];
var ROSTER=[],TIPS=[],CHART_SEQ=0,TIMER=null,HOVER=false,FLEET_AT=0,BOOTED=false;
var CACHE={};
/**
 * localStorage 只用来记住上次看的是哪个账户/页签，属于锦上添花。
 * 但浏览器在隐私模式或禁用站点数据时会让读写**直接抛异常**——不兜住的话
 * 整个脚本在第一行就死掉，看板变白屏。偏好丢了无所谓，页面必须能开。
 */
function lsGet(k,dflt){
  try{
    var v=localStorage.getItem(k);
    return v==null?dflt:v;
  }catch(e){return dflt}
}
function lsSet(k,v){
  try{localStorage.setItem(k,String(v))}catch(e){}
}
var STATE={
  scope:lsGet("qf_scope","fleet"),
  account:lsGet("qf_account",""),
  tab:lsGet("qf_tab",""),
  refresh:Number(lsGet("qf_refresh","10"))
};

/* ── 基础工具 ───────────────────────────────────────────────────── */
var $=function(s){return document.querySelector(s)};
function esc(s){
  return String(s==null?"":s).replace(/[&<>"']/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
  });
}
/**
 * 账户名绝不进 onclick 的 JS 字面量——名字来自配置，配置能在看板里改，
 * 一个带引号的名字就足以让处理器变成注入点。统一改成引用下标。
 */
var NAV=[];
function navRef(name){
  var i=NAV.indexOf(name);
  if(i<0){NAV.push(name);i=NAV.length-1}
  return i;
}
window.goNav=function(i,tab){go("account",NAV[i],tab)};
function fmt(v,d){
  var n=Number(v);
  if(!isFinite(n))return "-";
  return n.toLocaleString("zh-CN",{minimumFractionDigits:d==null?2:d,maximumFractionDigits:d==null?2:d});
}
function usd(v,d){var n=Number(v);return (n<0?"-$":"$")+fmt(Math.abs(n),d==null?2:d)}
function signed(v,d){var n=Number(v)||0;return (n>0?"+":"")+fmt(n,d==null?4:d)}
function pnlCls(v){var n=Number(v);return n>0?"up":n<0?"down":"dim"}
function tipId(html){TIPS.push(html);return TIPS.length-1}
function short(addr){
  var a=String(addr||"");
  return a.length>12?a.slice(0,6)+"…"+a.slice(-4):a;
}
function initials(name){
  var s=String(name||"?").replace(/[^0-9a-zA-Z一-龥]/g,"");
  return (s.slice(0,2)||"?").toUpperCase();
}
function acolor(name){
  for(var i=0;i<ROSTER.length;i++)if(ROSTER[i].name===name)return ACOLORS[i%ACOLORS.length];
  var h=0,s=String(name||"");
  for(var j=0;j<s.length;j++)h=(h*31+s.charCodeAt(j))>>>0;
  return ACOLORS[h%ACOLORS.length];
}
function accountOf(name){
  for(var i=0;i<ROSTER.length;i++)if(ROSTER[i].name===name)return ROSTER[i];
  return null;
}
function envBadge(testnet,big){
  return testnet
    ?'<span class="badge sim">测试网 · 模拟</span>'
    :'<span class="badge live">'+(big?"⚠ 主网 · 实盘":"主网 · 实盘")+"</span>";
}
function stratTags(s){
  s=s||{};
  var out=[];
  if(s.grid)out.push('<span class="tag grid">网格 '+esc(s.grid_symbol||"")+"</span>");
  if(!out.length)out.push('<span class="tag">空转</span>');
  return out.join(" ");
}
function toast(msg,ms){
  var el=$("#toast");
  el.innerHTML=msg;el.style.display="block";
  clearTimeout(el._t);el._t=setTimeout(function(){el.style.display="none"},ms||4000);
}

/* ── 取数 ───────────────────────────────────────────────────────── */
function token(){return lsGet("qf_token","")}
/** 这些接口的数据不属于任何单个账户，不能带 ?account=（回测报告读的是全局 data_dir）。 */
var GLOBAL_APIS=["/api/fleet","/api/accounts","/api/config","/api/backtests"];
function api(path,opts){
  opts=opts||{};
  var scoped=true;
  for(var g=0;g<GLOBAL_APIS.length;g++)if(path.indexOf(GLOBAL_APIS[g])===0)scoped=false;
  // 已显式带 account= 的路径不再覆盖（大盘视图要并发拉多个账户）
  if(STATE.account&&scoped&&path.indexOf("account=")<0){
    path+=(path.indexOf("?")>=0?"&":"?")+"account="+encodeURIComponent(STATE.account);
  }
  var headers={"Content-Type":"application/json"};
  if(opts.headers)for(var k in opts.headers)headers[k]=opts.headers[k];
  var tk=token();
  if(tk)headers.Authorization="Bearer "+tk;
  var req={};for(var k2 in opts)req[k2]=opts[k2];
  req.headers=headers;
  return fetch(path,req).then(function(resp){
    if(resp.status===401){
      var t=prompt("看板已启用访问令牌，请输入 QUANTFLOW_WEB_TOKEN：");
      if(!t)throw new Error("未授权");
      lsSet("qf_token",t);
      return api(path,opts);
    }
    return resp.json().then(function(data){
      if(!resp.ok)throw new Error(data&&data.error||resp.statusText);
      return data;
    });
  });
}

/* ── 作用域与页签 ───────────────────────────────────────────────── */
var ACCOUNT_TABS=[["overview","总览"],["decisions","决策"],["trades","交易"],["grid","网格"],["risk","风控"]];
var FLEET_TABS=[["overview","总览"],["compare","对比"],["risk","风控矩阵"]];
function tabsFor(scope){
  if(scope==="fleet")return FLEET_TABS;
  if(scope==="account")return ACCOUNT_TABS;
  return [];
}
function currentTab(){
  var list=tabsFor(STATE.scope);
  if(!list.length)return "";
  for(var i=0;i<list.length;i++)if(list[i][0]===STATE.tab)return STATE.tab;
  return list[0][0];
}
function go(scope,account,tab){
  STATE.scope=scope;
  if(account!=null)STATE.account=account;
  if(tab!=null)STATE.tab=tab;
  else if(!tabsFor(scope).length)STATE.tab="";
  lsSet("qf_scope",STATE.scope);
  lsSet("qf_account",STATE.account);
  lsSet("qf_tab",STATE.tab);
  // 只丢视图级缓存：大盘摘要要留给侧栏，否则每切一次账户侧栏就空一次
  CACHE.overview=null;CACHE.decisions=null;
  renderSide();
  render();
}
window.go=go;
window.goTab=function(t){STATE.tab=t;lsSet("qf_tab",t);render()};

/* ── 侧栏 ───────────────────────────────────────────────────────── */
function sideAccountCard(a){
  var f=(CACHE.fleetAccounts||{})[a.name]||null;
  var active=STATE.scope==="account"&&STATE.account===a.name;
  var color=acolor(a.name);
  var pnl=f?Number(f.realized_pnl_today)||0:null;
  return '<div class="acard'+(active?" active":"")+(a.testnet?"":" live")+'" style="--ac:'+color
    +'" onclick="goNav('+navRef(a.name)+')" title="'+esc(a.name)+'">'
    +'<div class="rail"></div>'
    +'<div class="top"><span class="avatar sm">'+esc(initials(a.name))+"</span>"
      +'<span class="nm">'+esc(a.name)+"</span>"
      +'<span class="grow"></span>'
      +'<span class="pulse'+(a.running?"":" off")+'"></span></div>'
    +'<div class="mid"><span class="eq num">'+(f?usd(f.equity):'<span class="dim2">—</span>')+"</span>"
      +'<span class="pl num '+(pnl==null?"dim2":pnlCls(pnl))+'">'+(pnl==null?"":signed(pnl))+"</span></div>"
    +'<div class="foot">'+(a.testnet?'<span class="badge sim">测试</span>':'<span class="badge live">实盘</span>')
      +'<span class="spark">'+(f&&f.equity_history&&f.equity_history.length>1
        ?sparkline(f.equity_history.map(function(e){return e.equity}),{h:22,color:color}):"")+"</span></div></div>";
}
function renderSide(){
  var live=0;
  for(var i=0;i<ROSTER.length;i++)if(!ROSTER[i].testnet)live++;
  var html='<div class="navwrap">'
    +'<div class="navitem'+(STATE.scope==="fleet"?" active":"")+'" onclick="go(\'fleet\')">'
      +'<span class="ico">◎</span><span>大盘总控</span><span class="grow"></span>'
      +'<span class="badge">'+ROSTER.length+"</span></div></div>"
    +'<h4>账户 '+(live?'· <span style="color:var(--live)">'+live+" 个实盘</span>":"· 全部模拟")+"</h4>"
    +ROSTER.map(sideAccountCard).join("")
    +'<h4>全局</h4><div class="navwrap">'
    +'<div class="navitem'+(STATE.scope==="backtest"?" active":"")+'" onclick="go(\'backtest\')">'
      +'<span class="ico">▤</span><span>回测</span></div>'
    +'<div class="navitem'+(STATE.scope==="config"?" active":"")+'" onclick="go(\'config\')">'
      +'<span class="ico">⚙</span><span>配置</span></div></div>';
  $("#side").innerHTML=html;
}

/* ── 作用域头 ───────────────────────────────────────────────────── */
function scopeHeader(o){
  // o={color,live,title,badges,metas,kpis,sub}
  return '<div class="scope'+(o.live?" live":"")+'" style="--ac:'+(o.color||"var(--acc)")+'">'
    +'<div class="id"><span class="avatar">'+esc(o.initials||"◎")+"</span>"
      +"<div><div class=\"nm\">"+esc(o.title)+"</div>"
      +'<div class="meta">'+(o.metas||[]).join("")+"</div></div></div>"
    +'<div style="display:flex;gap:6px;flex-wrap:wrap">'+(o.badges||[]).join("")+"</div>"
    +'<div class="kpi">'+(o.kpis||[]).map(function(k){
        return '<div><div class="k">'+esc(k[0])+'</div><div class="v num '+(k[2]||"")+'">'+k[1]+"</div></div>";
      }).join("")+"</div>"
    // sub：整行附加区（主网名义额闸进度条等），只在有内容时渲染
    +(o.sub?'<div class="sub">'+o.sub+"</div>":"")+"</div>";
}
function meta(label,value){return "<span>"+esc(label)+" <b>"+value+"</b></span>"}
function tabsBar(){
  var list=tabsFor(STATE.scope),cur=currentTab();
  if(!list.length)return "";
  return '<div class="tabs">'+list.map(function(t){
    return '<button class="'+(t[0]===cur?"active":"")+'" onclick="goTab(\''+t[0]+'\')">'+esc(t[1])+"</button>";
  }).join("")+"</div>";
}

/* ── 渲染节拍 ───────────────────────────────────────────────────── */
function setRefresh(v){
  STATE.refresh=Number(v)||0;
  lsSet("qf_refresh",STATE.refresh);
  renderTop();
  schedule();
}
window.setRefresh=setRefresh;
function renderTop(){
  var opts=[[0,"手动"],[5,"5 秒"],[10,"10 秒"],[30,"30 秒"],[60,"1 分钟"]];
  $("#refreshctl").innerHTML='<select onchange="setRefresh(this.value)">'+opts.map(function(o){
    return '<option value="'+o[0]+'"'+(STATE.refresh===o[0]?" selected":"")+">自动刷新："+o[1]+"</option>";
  }).join("")+"</select>";
}
function schedule(){
  clearTimeout(TIMER);
  if(!STATE.refresh)return;
  TIMER=setTimeout(function(){
    // 悬停在图表上或页面不可见时不打断：推迟一个短周期再试
    if(HOVER||document.hidden){schedule();return}
    render();
  },STATE.refresh*1000);
}
function render(){
  clearTimeout(TIMER);
  TIPS=[];CHART_SEQ=0;
  var scope=STATE.scope,tab=currentTab();
  var p;
  if(scope==="account"&&!accountOf(STATE.account)&&ROSTER.length){
    STATE.account=ROSTER[0].name;
    lsSet("qf_account",STATE.account);
  }
  document.title=(scope==="account"?STATE.account+" · ":"")+"Quant Flow 看板";
  var acct=scope==="account"?accountOf(STATE.account):null;
  document.body.className=acct&&!acct.testnet?"livemode":"";
  $("#liverail").style.display=acct&&!acct.testnet?"block":"none";
  try{
    if(scope==="fleet")p=viewFleetScope(tab);
    else if(scope==="account")p=viewAccountScope(tab);
    else if(scope==="backtest")p=viewBacktest();
    else if(scope==="config")p=viewConfig();
    else p=Promise.resolve('<div class="panel"><div class="empty">未知视图</div></div>');
  }catch(e){p=Promise.reject(e)}
  Promise.resolve(p).then(function(html){
    $("#view").innerHTML=html;
    $("#ts").textContent="更新于 "+new Date().toLocaleTimeString("zh-CN",{hour12:false});
    maybeRefreshFleet();
    schedule();
  }).catch(function(e){
    $("#view").innerHTML='<div class="panel"><h3>加载失败</h3><div class="empty">'+esc(e&&e.message||e)+"</div></div>";
    $("#ts").textContent="加载失败 "+new Date().toLocaleTimeString("zh-CN",{hour12:false});
    schedule();
  });
}
window.render=render;
/** 侧栏的净值/收益来自大盘接口（较重），非大盘页每 60 秒兜底刷新一次。 */
function maybeRefreshFleet(){
  if(STATE.scope==="fleet")return;
  if(Date.now()-FLEET_AT<60000)return;
  FLEET_AT=Date.now();
  api("/api/fleet").then(function(f){
    cacheFleet(f);
    renderSide();
  }).catch(function(){});
}
function cacheFleet(f){
  FLEET_AT=Date.now();
  var map={};
  (f.accounts||[]).forEach(function(a){map[a.name]=a});
  CACHE.fleetAccounts=map;
  CACHE.fleet=f;
}

/* ── 浮层（图表 tooltip）────────────────────────────────────────── */
function bindTip(){
  var tip=$("#tip");
  document.addEventListener("mousemove",function(e){
    var host=e.target&&e.target.closest?e.target.closest("[data-tip]"):null;
    if(!host){tip.style.display="none";return}
    var html=TIPS[Number(host.getAttribute("data-tip"))];
    if(html==null){tip.style.display="none";return}
    tip.innerHTML=html;
    tip.style.display="block";
    var r=tip.getBoundingClientRect();
    var x=e.clientX+14,y=e.clientY+14;
    if(x+r.width>window.innerWidth-8)x=e.clientX-r.width-14;
    if(y+r.height>window.innerHeight-8)y=e.clientY-r.height-14;
    tip.style.left=Math.max(6,x)+"px";
    tip.style.top=Math.max(6,y)+"px";
  },true);
  document.addEventListener("mouseover",function(e){
    if(e.target&&e.target.closest&&e.target.closest(".chart,.panel .scroll"))HOVER=true;
  },true);
  document.addEventListener("mouseout",function(e){
    if(e.target&&e.target.closest&&e.target.closest(".chart,.panel .scroll"))HOVER=false;
  },true);
}

/* ── 启动 ───────────────────────────────────────────────────────── */
function boot(){
  if(BOOTED)return;
  BOOTED=true;
  bindTip();
  renderTop();
  api("/api/accounts").then(function(list){
    ROSTER=list||[];
    if(!STATE.account&&ROSTER.length)STATE.account=ROSTER[0].name;
    if(STATE.scope==="fleet"&&ROSTER.length===1)STATE.scope="account";
    renderSide();
    render();
  }).catch(function(e){
    $("#side").innerHTML='<div class="empty">账户列表加载失败</div>';
    $("#view").innerHTML='<div class="panel"><h3>连接失败</h3><div class="empty">'+esc(e&&e.message||e)+"</div></div>";
  });
}
`;
