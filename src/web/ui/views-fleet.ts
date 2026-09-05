/**
 * 大盘作用域视图：把 N 个账户放在同一张图里比较，同时保持「谁是谁」一眼可辨。
 *
 * 取数走 /api/fleet（一次拿全部账户摘要），渲染是纯函数 fleetHtml(f,tab)——
 * 排序与「绝对值/归一化」切换只重排已有数据，不重新打交易所。
 */

export const VIEWS_FLEET_JS = String.raw`
var FLEET_MODE="abs";
var FLEET_SORT={key:"equity",dir:-1};
window.setFleetMode=function(m){FLEET_MODE=m;if(CACHE.fleet)$("#view").innerHTML=fleetHtml(CACHE.fleet,currentTab())};
window.sortFleet=function(k){
  FLEET_SORT=FLEET_SORT.key===k?{key:k,dir:-FLEET_SORT.dir}:{key:k,dir:-1};
  if(CACHE.fleet)$("#view").innerHTML=fleetHtml(CACHE.fleet,currentTab());
};

function viewFleetScope(tab){
  if(tab==="risk")return viewFleetRisk();
  return api("/api/fleet").then(function(f){
    cacheFleet(f);
    renderSide();
    return fleetHtml(f,tab);
  });
}

function fleetScopeHeader(f){
  var t=f.totals||{};
  return scopeHeader({
    color:"var(--acc)",initials:"◎",title:"大盘总控",
    live:(t.mainnet_count||0)>0,
    metas:[
      meta("账户",(t.count||0)+" 个"),
      meta("运行中",'<span class="'+((t.running||0)===(t.count||0)?"up":"down")+'">'+(t.running||0)+"/"+(t.count||0)+"</span>"),
      meta("环境",'<span class="dim">测试 '+(t.testnet_count||0)+"</span> · "
        +((t.mainnet_count||0)?'<span class="down">主网 '+t.mainnet_count+"</span>":'<span class="dim">主网 0</span>'))
    ],
    badges:(t.mainnet_count||0)?['<span class="badge live">⚠ 含 '+t.mainnet_count+" 个实盘账户</span>"]:['<span class="badge sim">全部模拟盘</span>'],
    kpis:[
      ["总净值",usd(t.equity),""],
      ["今日已实现",signed(t.realized_pnl_today),pnlCls(t.realized_pnl_today)],
      ["累计已实现",signed(t.realized_pnl_total),pnlCls(t.realized_pnl_total)]
    ]
  });
}

function fleetHtml(f,tab){
  var accounts=(f.accounts||[]).slice();
  if(!accounts.length){
    return fleetScopeHeader(f)+tabsBar()+'<div class="panel"><div class="empty">未装配任何账户</div></div>';
  }
  return fleetScopeHeader(f)+tabsBar()+(tab==="compare"?fleetCompareHtml(f,accounts):fleetOverviewHtml(f,accounts));
}

/** 多账户净值对比。归一化模式把每个账户的首个快照拉平到 100，规模不同也能比形状。 */
function fleetEquitySeries(accounts,mode){
  return accounts.map(function(a){
    var hist=(a.equity_history||[]).filter(function(e){return isFinite(Number(e.equity))});
    var base=hist.length?Number(hist[0].equity)||0:0;
    return {
      name:a.name,color:acolor(a.name),
      pts:hist.map(function(e){
        var v=Number(e.equity);
        return [tms(e.t),mode==="pct"?(base>0?v/base*100:100):v];
      }).filter(function(p){return isFinite(p[0])})
    };
  });
}

function fleetOverviewHtml(f,accounts){
  var t=f.totals||{};
  var cards=[
    ["账户总数",(t.count||0)+'<span class="dim" style="font-size:12px">（测 '+(t.testnet_count||0)+" / 主 "+(t.mainnet_count||0)+"）</span>",""],
    ["总净值",usd(t.equity),""],
    ["总可用",usd(t.available),""],
    ["总未实现",usd(t.unrealized_pnl),pnlCls(t.unrealized_pnl)],
    ["今日已实现",signed(t.realized_pnl_today),pnlCls(t.realized_pnl_today)],
    ["累计已实现",signed(t.realized_pnl_total),pnlCls(t.realized_pnl_total)]
  ].map(function(c){
    return '<div class="card"><div class="k">'+esc(c[0])+'</div><div class="v num '+c[2]+'">'+c[1]+"</div></div>";
  }).join("");

  var series=fleetEquitySeries(accounts,FLEET_MODE);
  var chart='<div class="panel"><h3>净值曲线对比'
    +'<span class="hint">每条线一个账户，颜色与左侧账户栏一致</span>'
    +'<span class="grow" style="flex:1"></span>'
    +'<span class="seg"><button class="'+(FLEET_MODE==="abs"?"active":"")+'" onclick="setFleetMode(\'abs\')">绝对值</button>'
    +'<button class="'+(FLEET_MODE==="pct"?"active":"")+'" onclick="setFleetMode(\'pct\')">归一化</button></span></h3>'
    +chartLine({series:series,h:270,unit:FLEET_MODE==="pct"?"":"",dec:2,
      emptyText:"暂无净值快照（引擎每轮循环写一条）"})+"</div>";

  var donut='<div class="panel"><h3>净值构成</h3>'
    +chartDonut(accounts.map(function(a){return {label:a.name,value:a.equity,color:acolor(a.name)}}),
      {h:250,centerLabel:"总净值 USD",name:"净值"})+"</div>";

  var bars='<div class="panel"><h3>今日已实现盈亏 <span class="hint">来自各账户 trades 归因记录</span></h3>'
    +chartBarsH(accounts.map(function(a){
      return {label:a.name,value:a.realized_pnl_today,dotColor:acolor(a.name),
        tip:'<div class="r"><span>今日成交</span><b>'+(a.trades_today||0)+" 笔</b></div>"};
    }),{name:"今日已实现",dec:4})+"</div>";
  var bars2='<div class="panel"><h3>未实现盈亏 <span class="hint">当前持仓的浮动盈亏</span></h3>'
    +chartBarsH(accounts.map(function(a){
      return {label:a.name,value:a.unrealized_pnl,dotColor:acolor(a.name),
        tip:'<div class="r"><span>持仓</span><b>'+(a.positions_query_failed?"查询失败":(a.positions_count||0)+" 个")+"</b></div>"};
    }),{name:"未实现",dec:2,unit:""})+"</div>";

  return '<div class="cards">'+cards+"</div>"
    +'<div class="g21">'+chart+donut+"</div>"
    +'<div class="g2">'+bars+bars2+"</div>"
    +fleetTableHtml(accounts);
}

function fleetCompareHtml(f,accounts){
  var series=fleetEquitySeries(accounts,"pct");
  var rank=accounts.slice().sort(function(a,b){return (Number(b.realized_pnl_total)||0)-(Number(a.realized_pnl_total)||0)});
  return '<div class="panel"><h3>净值归一化对比 <span class="hint">各账户首个快照 = 100，只看走势形状，不受本金规模影响</span></h3>'
      +chartLine({series:series,h:300,dec:2,emptyText:"暂无净值快照"})+"</div>"
    +'<div class="g2">'
    +'<div class="panel"><h3>累计已实现盈亏排行</h3>'
      +chartBarsH(rank.map(function(a){return {label:a.name,value:a.realized_pnl_total,dotColor:acolor(a.name)}}),{name:"累计已实现",dec:4})+"</div>"
    +'<div class="panel"><h3>今日活跃度 <span class="hint">今日成交笔数</span></h3>'
      +chartBarsV(accounts.map(function(a){return {label:a.name,value:a.trades_today||0,color:acolor(a.name)}}),{h:220,name:"今日成交"})+"</div>"
    +"</div>"
    +'<div class="panel"><h3>账户口径对照 <span class="hint">同一指标横向排开，异常值一眼看出</span></h3><div class="scroll">'
    +'<table><tr><th>账户</th><th class="r">净值</th><th class="r">可用</th><th class="r">可用占比</th>'
    +'<th class="r">未实现</th><th class="r">今日已实现</th><th class="r">累计已实现</th><th class="r">今日成交</th></tr>'
    +accounts.map(function(a){
      var ratio=Number(a.equity)>0?Number(a.available)/Number(a.equity)*100:0;
      return '<tr><td><div class="rowid"><span class="bar" style="background:'+acolor(a.name)+'"></span>'+esc(a.name)+"</div></td>"
        +'<td class="r num">'+usd(a.equity)+"</td>"
        +'<td class="r num">'+usd(a.available)+"</td>"
        +'<td class="r num '+(ratio<20?"down":"dim")+'">'+fmt(ratio,1)+"%</td>"
        +'<td class="r num '+pnlCls(a.unrealized_pnl)+'">'+usd(a.unrealized_pnl)+"</td>"
        +'<td class="r num '+pnlCls(a.realized_pnl_today)+'">'+signed(a.realized_pnl_today)+"</td>"
        +'<td class="r num '+pnlCls(a.realized_pnl_total)+'">'+signed(a.realized_pnl_total)+"</td>"
        +'<td class="r num">'+(a.trades_today||0)+"</td></tr>";
    }).join("")+"</table></div></div>";
}

function fleetTableHtml(accounts){
  var key=FLEET_SORT.key,dir=FLEET_SORT.dir;
  var rows=accounts.slice().sort(function(a,b){
    var x=a[key],y=b[key];
    if(typeof x==="string"||typeof y==="string")return String(x).localeCompare(String(y))*dir;
    return ((Number(y)||0)-(Number(x)||0))*(dir<0?1:-1);
  });
  var th=function(k,label,cls){
    return '<th class="s '+(cls||"")+'" onclick="sortFleet(\''+k+'\')">'+esc(label)
      +(FLEET_SORT.key===k?(FLEET_SORT.dir<0?" ↓":" ↑"):"")+"</th>";
  };
  return '<div class="panel"><h3>账户矩阵 <span class="hint">点击行进入该账户的独立视图；点击表头排序</span></h3><div class="scroll">'
    +"<table><tr>"+th("name","账户")+"<th>环境</th><th>策略</th>"+th("equity","净值","r")+th("available","可用","r")
    +th("unrealized_pnl","未实现","r")+th("realized_pnl_today","今日已实现","r")+th("realized_pnl_total","累计已实现","r")
    +th("trades_today","今日成交","r")+"<th class=\"r\">持仓</th><th>净值走势</th><th>状态</th></tr>"
    +rows.map(function(a){
      var color=acolor(a.name);
      return '<tr class="click" onclick="goNav('+navRef(a.name)+')">'
        +'<td><div class="rowid"><span class="bar" style="background:'+color+'"></span>'
          +'<span class="avatar sm" style="--ac:'+color+';background:'+color+'">'+esc(initials(a.name))+"</span>"
          +"<div><div><b>"+esc(a.name)+'</b></div><div class="dim2 mono" style="font-size:10.5px">'+esc(short(a.address))+"</div></div></div></td>"
        +"<td>"+envBadge(a.testnet)+"</td>"
        +"<td>"+stratTags(a.strategies)+"</td>"
        +'<td class="r num">'+(a.balance_status==="ok"?usd(a.equity):'<span class="down">查询失败</span>')+"</td>"
        +'<td class="r num">'+usd(a.available)+"</td>"
        +'<td class="r num '+pnlCls(a.unrealized_pnl)+'">'+usd(a.unrealized_pnl)+"</td>"
        +'<td class="r num '+pnlCls(a.realized_pnl_today)+'">'+signed(a.realized_pnl_today)+"</td>"
        +'<td class="r num '+pnlCls(a.realized_pnl_total)+'">'+signed(a.realized_pnl_total)+"</td>"
        +'<td class="r num">'+(a.trades_today||0)+"</td>"
        +'<td class="r num">'+(a.positions_query_failed?'<span class="down">?</span>':(a.positions_count||0))+"</td>"
        +'<td style="width:130px">'+sparkline((a.equity_history||[]).map(function(e){return e.equity}),{h:24,color:color})+"</td>"
        +"<td>"+(a.running?'<span class="badge ok">运行中</span>':'<span class="badge bad">已停止</span>')+"</td></tr>";
    }).join("")+"</table></div></div>";
}

/** 风控矩阵：并发拉每个账户的保护链，把「谁被暂停了」摆在同一屏。 */
function viewFleetRisk(){
  return Promise.all(ROSTER.map(function(a){
    return api("/api/protections?account="+encodeURIComponent(a.name))
      .then(function(rows){return {account:a,rows:rows||[]}})
      .catch(function(e){return {account:a,error:String(e&&e.message||e),rows:[]}});
  })).then(function(list){
    var f=CACHE.fleet||{totals:{count:ROSTER.length}};
    return fleetScopeHeader(f)+tabsBar()+fleetRiskHtml(list);
  });
}
function fleetRiskHtml(list){
  if(!list.length)return '<div class="panel"><div class="empty">无账户</div></div>';
  var names={};
  list.forEach(function(x){x.rows.forEach(function(p){names[p.name]=1})});
  var cols=Object.keys(names);
  if(!cols.length){
    return '<div class="panel"><h3>风控矩阵</h3><div class="empty">所有账户都未加载保护插件（protections: []）</div></div>';
  }
  return '<div class="panel"><h3>风控矩阵 <span class="hint">每行一个账户，每列一个保护插件——被暂停/锁定的账户在这里最先暴露</span></h3><div class="scroll">'
    +"<table><tr><th>账户</th>"+cols.map(function(c){return "<th>"+esc(c)+"</th>"}).join("")+"</tr>"
    +list.map(function(x){
      var byName={};
      x.rows.forEach(function(p){byName[p.name]=p});
      return '<tr class="click" onclick="goNav('+navRef(x.account.name)+',\'risk\')">'
        +'<td><div class="rowid"><span class="bar" style="background:'+acolor(x.account.name)+'"></span><b>'
          +esc(x.account.name)+"</b> "+envBadge(x.account.testnet)+"</div></td>"
        +cols.map(function(c){
          var p=byName[c];
          if(!p)return '<td class="dim2">—</td>';
          return "<td>"+protectionCell(p)+"</td>";
        }).join("")+"</tr>";
    }).join("")+"</table></div>"
    +(list.some(function(x){return x.error})?'<div class="dim" style="margin-top:8px">部分账户读取失败：'
      +esc(list.filter(function(x){return x.error}).map(function(x){return x.account.name}).join("、"))+"</div>":"")
    +"</div>";
}
`;
