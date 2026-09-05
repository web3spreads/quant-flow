/**
 * 账户作用域视图：总览 / 决策 / 交易 / 网格 / 风控。
 *
 * 每个页面顶部都重复一遍账户身份（色条 + 头像 + 环境徽标 + 地址 + 策略），
 * 主网账户额外走 .live 警示态。渲染同样是纯函数（xxxHtml），过滤器只重排缓存。
 */

export const VIEWS_ACCOUNT_JS = String.raw`
var DEC_FILTER={strategy:"",status:"",symbol:""};
window.setDecFilter=function(k,v){
  DEC_FILTER[k]=v;
  if(CACHE.decisions)$("#view").innerHTML=accountShell(CACHE.overview)+decisionsHtml(CACHE.decisions);
};

function accountScopeHeader(o){
  var a=accountOf(STATE.account)||{};
  var e=(o&&o.engine)||{};
  var color=acolor(STATE.account);
  var b=(o&&o.balance)||{};
  var f=(CACHE.fleetAccounts||{})[STATE.account];
  return scopeHeader({
    color:color,initials:initials(STATE.account),title:STATE.account,
    live:!(e.testnet==null?a.testnet:e.testnet),
    metas:[
      meta("地址",'<span class="mono">'+esc(short(a.address||e.account))+"</span>"),
      meta("策略",stratTags(a.strategies||{grid:e.grid_enabled,grid_symbol:(e.symbols||[])[0]})),
      meta("标的",esc((e.symbols||(a.strategies||{}).symbols||[]).join(" / ")||"—")),
      meta("模型",'<span class="dim">'+esc(e.llm||a.llm||"—")+"</span>"),
      meta("数据目录",'<span class="mono dim2">'+esc(a.data_dir||"—")+"</span>")
    ],
    badges:[
      envBadge(e.testnet==null?a.testnet:e.testnet,true),
      (e.running?'<span class="badge ok"><span class="pulse"></span>运行中</span>':'<span class="badge bad">已停止</span>')
    ],
    kpis:[
      ["账户净值",usd(b.total!=null?b.total:(f?f.equity:0)),""],
      ["未实现",usd(b.unrealized_pnl!=null?b.unrealized_pnl:(f?f.unrealized_pnl:0)),pnlCls(b.unrealized_pnl)],
      // 今日收益来自大盘摘要；还没拉到就显示占位，绝不用 0 冒充「今天没赚没亏」
      ["今日已实现",f?signed(f.realized_pnl_today):'<span class="dim2">—</span>',f?pnlCls(f.realized_pnl_today):""]
    ]
  });
}
function accountShell(o){return accountScopeHeader(o)+tabsBar()}

function viewAccountScope(tab){
  return api("/api/overview").then(function(o){
    CACHE.overview=o;
    if(tab==="decisions")return api("/api/decisions?limit=300").then(function(rows){
      CACHE.decisions=rows;return accountShell(o)+decisionsHtml(rows);
    });
    if(tab==="trades")return api("/api/trades?limit=400").then(function(rows){
      return accountShell(o)+tradesHtml(rows);
    });
    if(tab==="grid")return api("/api/grid").then(function(g){
      return accountShell(o)+gridHtml(g);
    });
    if(tab==="risk")return api("/api/protections").then(function(rows){
      return accountShell(o)+riskHtml(rows||[]);
    });
    return api("/api/equity?limit=400").then(function(eq){
      return accountShell(o)+overviewHtml(o,eq);
    });
  });
}

/* ── 总览 ───────────────────────────────────────────────────────── */
function overviewHtml(o,eq){
  var b=o.balance||{},pos=o.positions||[],ords=o.open_orders||[];
  var color=acolor(STATE.account);
  var hist=(eq||[]).slice().reverse();
  var series=[
    {name:"净值",color:color,pts:hist.map(function(r){return [tms(r.timestamp),Number(r.equity)]})
      .filter(function(p){return isFinite(p[0])&&isFinite(p[1])})},
    {name:"可用",color:"#5f6d8c",pts:hist.map(function(r){return [tms(r.timestamp),Number(r.available)]})
      .filter(function(p){return isFinite(p[0])&&isFinite(p[1])})}
  ];
  var notional=hist.map(function(r){return [tms(r.timestamp),Number(r.position_notional)||0]})
    .filter(function(p){return isFinite(p[0])});
  var cards=[
    ["账户总值",usd(b.total),"",b.status&&b.status!=="ok"?'<span class="down">查询异常</span>':""],
    ["可用余额",usd(b.available),"",""],
    ["未实现盈亏",usd(b.unrealized_pnl),pnlCls(b.unrealized_pnl),""],
    ["持仓 / 挂单",pos.length+" / "+ords.length,"",
      (o.positions_query_failed||o.open_orders_query_failed)?'<span class="down">查询失败</span>':""]
  ];
  if(o.strategies&&o.strategies.grid){
    var h=o.strategies.grid.health||{};
    cards.push(["网格 "+o.strategies.grid.symbol+" · 连败/空转",
      (h.llm_failure_streak||0)+" / "+(h.idle_streak||0),(h.llm_failure_streak?"down":"up"),""]);
  }
  return '<div class="cards">'+cards.map(function(c){
      return '<div class="card"><div class="k">'+esc(c[0])+'</div><div class="v num '+c[2]+'">'+c[1]+"</div>"
        +(c[3]?'<div class="s">'+c[3]+"</div>":"")+"</div>";
    }).join("")+"</div>"
    +'<div class="panel"><h3>净值曲线 <span class="hint">'+hist.length+' 个快照 · 灰线为可用余额，与净值的差即占用保证金</span></h3>'
      +chartLine({series:series,h:280,area:true,dec:2,emptyText:"暂无净值快照"})+"</div>"
    +'<div class="g2">'
      +'<div class="panel"><h3>持仓名义额</h3>'
        +chartLine({series:[{name:"名义额",color:"#a78bfa",pts:notional}],h:180,area:true,zero:true,dec:0,emptyText:"暂无数据"})+"</div>"
      +'<div class="panel"><h3>当前持仓'+(o.positions_query_failed?' <span class="badge bad">查询失败</span>':"")+"</h3>"
        +(pos.length?'<div class="scroll"><table><tr><th>交易对</th><th>方向</th><th class="r">数量</th><th class="r">入场价</th>'
          +'<th class="r">名义额</th><th class="r">未实现</th><th class="r">杠杆</th></tr>'
          +pos.map(function(p){
            var szi=Number(p.szi);
            return "<tr><td><b>"+esc(p.coin)+'</b></td><td class="'+(szi>0?"up":"down")+'">'+(szi>0?"多":"空")+"</td>"
              +'<td class="r num">'+esc(Math.abs(szi))+'</td><td class="r num">'+fmt(p.entryPx,4)+"</td>"
              +'<td class="r num">'+usd(p.positionValue)+'</td><td class="r num '+pnlCls(p.unrealizedPnl)+'">'+usd(p.unrealizedPnl)+"</td>"
              +'<td class="r num">'+esc(p.leverage&&p.leverage.value||"-")+"x</td></tr>";
          }).join("")+"</table></div>":'<div class="empty">无持仓</div>')+"</div>"
    +"</div>"
    +'<div class="panel"><h3>交易所挂单'+(o.open_orders_query_failed?' <span class="badge bad">查询失败</span>':"")
      +' <span class="hint">直接来自交易所，不是本地状态</span></h3>'
      +(ords.length?'<div class="scroll"><table><tr><th>oid</th><th>交易对</th><th>方向</th><th class="r">价格</th>'
        +'<th class="r">数量</th><th>类型</th></tr>'
        +ords.map(function(x){
          return '<tr><td class="num dim">'+esc(x.oid)+"</td><td><b>"+esc(x.coin)+'</b></td><td class="'+(x.side==="B"?"up":"down")+'">'
            +(x.side==="B"?"买":"卖")+'</td><td class="r num">'+fmt(x.limitPx,4)+'</td><td class="r num">'+esc(x.sz)+"</td><td>"
            +(x.reduceOnly?'<span class="tag">仅减仓</span>':(x.orderType&&x.orderType.trigger?'<span class="tag">条件单</span>':'<span class="tag">限价</span>'))
            +"</td></tr>";
        }).join("")+"</table></div>":'<div class="empty">无挂单</div>')+"</div>";
}

/* ── 决策 ───────────────────────────────────────────────────────── */
function decisionsHtml(rows){
  rows=rows||[];
  var all=rows;
  var filtered=rows.filter(function(r){
    if(DEC_FILTER.strategy&&r.strategy!==DEC_FILTER.strategy)return false;
    if(DEC_FILTER.status==="fail"&&r.status==="SUCCESS")return false;
    if(DEC_FILTER.symbol&&r.symbol!==DEC_FILTER.symbol)return false;
    return true;
  });
  var counts={},symbols={},ok=0,conf=0,confN=0;
  all.forEach(function(r){
    counts[r.decision]=(counts[r.decision]||0)+1;
    if(r.symbol)symbols[r.symbol]=1;
    if(r.status==="SUCCESS")ok++;
    var c=Number(r.confidence);
    if(isFinite(c)&&c>0){conf+=c;confN++}
  });
  var dist=Object.keys(counts).map(function(k){
    return {label:k,value:counts[k],color:["UPDATE_GRID","KEEP_GRID"].indexOf(k)>=0?"#a78bfa":"#38bdf8"};
  }).sort(function(a,b){return b.value-a.value});
  var seg=function(k,v,label){
    return '<button class="'+(DEC_FILTER[k]===v?"active":"")+'" onclick="setDecFilter(\''+k+'\',\''+v+'\')">'+esc(label)+"</button>";
  };
  var cards=[
    ["决策条数",String(all.length),""],
    ["执行成功率",all.length?fmt(ok/all.length*100,1)+"%":"-",ok===all.length?"up":"down"],
    ["平均置信度",confN?fmt(conf/confN,2):"-",""],
    ["异常决策",String(all.length-ok),(all.length-ok)?"down":"up"]
  ].map(function(c){return '<div class="card"><div class="k">'+esc(c[0])+'</div><div class="v num '+c[2]+'">'+c[1]+"</div></div>"}).join("");
  return '<div class="cards">'+cards+"</div>"
    +'<div class="panel"><h3>决策类型分布 <span class="hint">UPDATE_GRID=重建 · KEEP_GRID=维持</span></h3>'
      +chartBarsV(dist,{h:190,name:"次数"})+"</div>"
    +'<div class="panel"><h3>决策时间线 <span class="hint">展开可见 prompt 与 AI 原始回复</span>'
      +'<span style="flex:1"></span>'
      +'<span class="seg">'+seg("status","","不筛状态")+seg("status","fail","只看异常")+"</span></h3>"
    +(filtered.length?'<div class="scroll"><table><tr><th>时间</th><th>策略</th><th>交易对</th><th>决策</th>'
      +'<th class="r">置信度</th><th>状态</th><th>理由 / 详情</th></tr>'
      +filtered.map(function(r){
        var reason=String((r.action_details&&r.action_details.reason)||r.error_message||"");
        return '<tr><td class="dim num">'+esc(tfull(tms(r.timestamp)))+'</td>'
          +'<td><span class="tag '+esc(r.strategy)+'">'+(r.strategy==="grid"?"网格":esc(r.strategy))+"</span></td>"
          +"<td>"+esc(r.symbol)+"</td><td><b>"+esc(r.decision)+'</b></td>'
          +'<td class="r num">'+fmt(r.confidence)+"</td>"
          +"<td>"+(r.status==="SUCCESS"?'<span class="badge ok">正常</span>':'<span class="badge bad">'+esc(r.status)+"</span>")+"</td>"
          +"<td><details><summary>"+esc(reason.slice(0,90)||"展开")+"</summary>"
          +"<pre>"+esc(JSON.stringify({action_details:r.action_details,error:r.error_message,market:r.market_data},null,1))+"</pre>"
          +(r.ai_response?"<pre>AI 原始回复：\n"+esc(String(r.ai_response).slice(0,4000))+"</pre>":"")
          +(r.prompt&&r.prompt!=="[GridAgent]"?"<details><summary>Prompt</summary><pre>"+esc(String(r.prompt).slice(0,6000))+"</pre></details>":"")
          +"</details></td></tr>";
      }).join("")+"</table></div>":'<div class="empty">没有符合筛选条件的决策</div>')+"</div>";
}

/* ── 交易 ───────────────────────────────────────────────────────── */
function tradesHtml(rows){
  rows=(rows||[]).slice();
  var withPnl=rows.filter(function(r){return r.pnl!=null&&isFinite(Number(r.pnl))});
  var wins=withPnl.filter(function(r){return Number(r.pnl)>0}).length;
  var total=withPnl.reduce(function(a,r){return a+Number(r.pnl)},0);
  var fees=rows.reduce(function(a,r){return a+(Number(r.fee)||0)},0);
  var known=rows.filter(function(r){return r.crossed!=null});
  var taker=known.filter(function(r){return r.crossed}).length;
  var chron=rows.slice().reverse();
  var cum=0;
  var curve=chron.filter(function(r){return r.pnl!=null&&isFinite(Number(r.pnl))}).map(function(r){
    cum+=Number(r.pnl);
    return [tms(r.timestamp),cum];
  }).filter(function(p){return isFinite(p[0])});
  var cards=[
    ["记录条数",String(rows.length),""],
    ["已归因盈亏",String(withPnl.length)+" 笔",""],
    ["胜率",withPnl.length?fmt(wins/withPnl.length*100,1)+"%":"-",withPnl.length&&wins/withPnl.length>=0.5?"up":"down"],
    ["累计已实现",signed(total),pnlCls(total)],
    ["累计手续费",fmt(fees,4),fees>0?"down":"dim"],
    ["taker 占比",known.length?fmt(taker/known.length*100,0)+"%":"-",known.length&&taker/known.length>0.8?"down":""]
  ].map(function(c){return '<div class="card"><div class="k">'+esc(c[0])+'</div><div class="v num '+c[2]+'">'+c[1]+"</div></div>"}).join("");
  return '<div class="cards">'+cards+"</div>"
    +'<div class="g2">'
    +'<div class="panel"><h3>累计已实现盈亏 <span class="hint">按 trades 归因记录顺序累加</span></h3>'
      +chartLine({series:[{name:"累计盈亏",color:acolor(STATE.account),pts:curve}],h:220,area:true,zero:true,dec:4,
        emptyText:"暂无带盈亏归因的成交"})+"</div>"
    +'<div class="panel"><h3>单笔盈亏分布</h3>'
      +chartHist(withPnl.map(function(r){return Number(r.pnl)}),{h:220,dec:4})+"</div>"
    +"</div>"
    +'<div class="panel"><h3>成交与订单事件 <span class="hint">最近 '+rows.length+" 条</span></h3>"
    +(rows.length?'<div class="scroll"><table><tr><th>时间</th><th>动作</th><th>交易对</th><th class="r">数量</th>'
      +'<th class="r">价格</th><th>状态</th><th class="r">盈亏</th><th class="r">手续费</th><th>归因</th></tr>'
      +rows.map(function(r){
        return '<tr><td class="dim num">'+esc(tfull(tms(r.timestamp)))+"</td><td>"+esc(r.action)+"</td>"
          +"<td><b>"+esc(r.symbol)+'</b></td><td class="r num">'+esc(r.amount)+'</td><td class="r num">'+fmt(r.price,4)+"</td>"
          +"<td>"+(String(r.status).toUpperCase()==="SUCCESS"?'<span class="badge ok">成功</span>':'<span class="badge bad">'+esc(r.status)+"</span>")+"</td>"
          +'<td class="r num '+pnlCls(r.pnl)+'">'+(r.pnl==null?'<span class="dim2">—</span>':signed(r.pnl))+"</td>"
          +'<td class="r num dim">'+(r.fee==null?"—":fmt(r.fee,4))+"</td>"
          +'<td class="dim">'+esc(r.reason||"")+(r.crossed!=null?' <span class="tag">'+(r.crossed?"taker":"maker")+"</span>":"")+"</td></tr>";
      }).join("")+"</table></div>":'<div class="empty">暂无成交记录</div>')+"</div>";
}

/* ── 网格 ───────────────────────────────────────────────────────── */
function gridHtml(g){
  if(!g||!g.enabled){
    return '<div class="panel"><div class="empty">本账户未启用网格策略（可在「配置」页开启 trading.grid_enabled）</div></div>';
  }
  var pnl=g.pnl||{},cfg=g.config||{},lv=g.levels||[];
  var params=cfg.parameters||cfg;
  var states={};
  lv.forEach(function(l){states[l.state]=(states[l.state]||0)+1});
  var stateCn={IDLE:"空闲",OPEN_PENDING:"开仓挂单",OPEN_FILLED:"已开仓",CLOSE_PENDING:"平仓挂单",COMPLETED:"已完成"};
  var stateColor={IDLE:"#5f6d8c",OPEN_PENDING:"#38bdf8",OPEN_FILLED:"#2ebd85",CLOSE_PENDING:"#f0b90b",COMPLETED:"#a78bfa"};
  var cards=[
    ["当前价",fmt(g.current_price,4),""],
    ["已实现",signed(pnl.realized_pnl),pnlCls(pnl.realized_pnl)],
    ["未实现",signed(pnl.unrealized_pnl),pnlCls(pnl.unrealized_pnl)],
    ["净盈亏",signed(pnl.net_pnl),pnlCls(pnl.net_pnl)],
    ["完成轮回",String(pnl.completed_round_trips==null?"-":pnl.completed_round_trips),""],
    ["持仓层级",String(pnl.open_positions==null?"-":pnl.open_positions)+" / "+lv.length,""],
    ["持仓均价",pnl.avg_entry_price?fmt(pnl.avg_entry_price,4):"-",""],
    ["重建冷却",(g.rebuild_cooldown_remaining||0)+"s",""]
  ].map(function(c){return '<div class="card"><div class="k">'+esc(c[0])+'</div><div class="v num '+c[2]+'">'+c[1]+"</div></div>"}).join("");
  return '<div class="cards">'+cards+"</div>"
    +'<div class="g21">'
    +'<div class="panel"><h3>层级阶梯 <span class="hint">纵轴为价格 · 黄线为现价 · 实线=已开仓，虚线=挂单中，浅色=空闲</span></h3>'
      +chartLadder({levels:lv,current:g.current_price,lower:params.lower_price,upper:params.upper_price,dec:4})+"</div>"
    +'<div class="panel"><h3>层级状态分布</h3>'
      +chartBarsV(Object.keys(states).map(function(k){
        return {label:stateCn[k]||k,value:states[k],color:stateColor[k]||"#4d6bfe"};
      }),{h:200,name:"层数"})
      +'<div class="legend" style="margin-top:10px"><span class="li">区间 <b>'
      +esc(params.lower_price?fmt(params.lower_price,4)+" ~ "+fmt(params.upper_price,4):"—")+"</b></span>"
      +'<span class="li">格数 <b>'+esc(params.grid_num||lv.length)+"</b></span>"
      +'<span class="li">每格金额 <b>'+esc(params.amount_per_grid?fmt(params.amount_per_grid,2):"—")+"</b></span></div></div>"
    +"</div>"
    +'<div class="panel"><h3>层级明细（'+lv.length+" 层）</h3>"
    +(lv.length?'<div class="scroll"><table><tr><th>层</th><th>方向</th><th>状态</th><th class="r">格价</th>'
      +'<th class="r">开仓成交价</th><th class="r">持仓量</th><th class="r">轮次</th><th class="r">累计盈亏</th></tr>'
      +lv.map(function(l){
        return "<tr><td>"+esc(l.id)+'</td><td class="'+(l.side==="LONG"?"up":"down")+'">'+(l.side==="LONG"?"多":"空")+"</td>"
          +'<td><span class="tag" style="border-color:'+(stateColor[l.state]||"var(--line)")+';color:'+(stateColor[l.state]||"var(--dim)")+'">'
          +esc(stateCn[l.state]||l.state)+"</span></td>"
          +'<td class="r num">'+fmt(l.price,4)+'</td><td class="r num">'+(l.open_fill_price?fmt(l.open_fill_price,4):"—")+"</td>"
          +'<td class="r num">'+esc(l.open_fill_amount||"—")+'</td><td class="r num">'+(l.round_trip_count||0)+"</td>"
          +'<td class="r num '+pnlCls(l.cumulative_pnl)+'">'+signed(l.cumulative_pnl)+"</td></tr>";
      }).join("")+"</table></div>":'<div class="empty">暂无层级（等待 UPDATE_GRID 建网）</div>')+"</div>"
    +'<div class="g2">'
    +'<div class="panel"><h3>网格摘要</h3><pre>'+esc(g.summary)+"</pre></div>"
    +'<div class="panel"><h3>Triple Barrier 兜底 / 策略健康</h3><pre>'
      +esc(JSON.stringify({barrier:g.barrier,strategy_health:g.strategy_health,pending_emergency_close:g.pending_emergency_close},null,1))
      +"</pre></div></div>";
}

/* ── 风控 ───────────────────────────────────────────────────────── */
function protectionCell(p){
  var st=p.state||{};
  if(!p.enabled)return '<span class="badge">停用</span>';
  if(st.is_paused)return '<span class="badge bad">已暂停</span><div class="dim2" style="font-size:11px">'+esc(String(st.pause_reason||"").slice(0,40))+"</div>";
  var locked=st.locked_symbols?Object.keys(st.locked_symbols):[];
  if(locked.length)return '<span class="badge warn">锁定 '+locked.length+'</span><div class="dim2" style="font-size:11px">'+esc(locked.join(","))+"</div>";
  var extra="";
  if(st.global_losses!=null)extra="连亏 "+st.global_losses;
  else if(st.peak_equity!=null)extra="峰值 "+usd(st.peak_equity);
  else if(st.position_records)extra=Object.keys(st.position_records).length+" 个持仓在计时";
  else if(st.daily_start_equity!=null)extra="日起点 "+usd(st.daily_start_equity);
  return '<span class="badge ok">正常</span>'+(extra?'<div class="dim2" style="font-size:11px">'+esc(extra)+"</div>":"");
}
function riskHtml(rows){
  if(!rows.length){
    return '<div class="panel"><h3>账户保护链</h3><div class="empty">未加载任何保护插件（protections: []）——该账户没有自动风控</div></div>';
  }
  return '<div class="cards">'+rows.map(function(p){
      var st=p.state||{};
      var bad=!p.enabled?"":st.is_paused?"down":"";
      return '<div class="card'+(st.is_paused?" hl":"")+'" style="--ac:var(--down)"><div class="k">'+esc(p.name)+"</div>"
        +'<div class="v '+bad+'" style="font-size:15px">'+protectionCell(p)+"</div></div>";
    }).join("")+"</div>"
    +rows.map(function(p){
      return '<div class="panel"><h3>'+esc(p.name)+" "
        +(p.enabled?'<span class="badge ok">启用</span>':'<span class="badge">停用</span>')
        +'<span style="flex:1"></span><span class="hint">配置与持久化状态</span></h3>'
        +'<div class="g2"><div><div class="dim" style="font-size:11.5px;margin-bottom:4px">配置</div><pre>'
        +esc(JSON.stringify(p.config,null,1))+"</pre></div>"
        +'<div><div class="dim" style="font-size:11.5px;margin-bottom:4px">状态</div><pre>'
        +esc(JSON.stringify(p.state,null,1))+"</pre></div></div></div>";
    }).join("");
}
`;
