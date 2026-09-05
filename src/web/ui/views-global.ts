/**
 * 全局作用域视图：回测报告与配置表单（不属于任何单个账户）。
 *
 * 配置表单仍由 /api/config 返回的 Schemastery JSON 动态生成——改 Schema 即改表单，
 * 这条不能变。这里只做两件事：把 accounts 段从裸 JSON 升级成「可读的账户清单 +
 * JSON 编辑区」，以及给各配置段加锚点导航。
 */

export const VIEWS_GLOBAL_JS = String.raw`
function globalScopeHeader(title,desc,badges){
  return scopeHeader({
    color:"var(--acc)",initials:"⚙",title:title,
    metas:[meta("范围",'<span class="dim">全局（不区分账户）</span>'),meta("说明",'<span class="dim">'+esc(desc)+"</span>")],
    badges:badges||[],kpis:[]
  });
}

/* ── 回测 ───────────────────────────────────────────────────────── */
function viewBacktest(){
  return api("/api/backtests").then(function(r){
    return globalScopeHeader("回测报告","由 scripts/backtest-suite.mjs 产出，看板只读展示")+backtestHtml(r);
  });
}
function backtestHtml(r){
  if(!r||r.empty){
    return '<div class="panel"><div class="empty">'+esc((r&&r.hint)||"尚无回测报告")+"</div></div>";
  }
  var runs=r.runs||[],sum=r.summary||{};
  var pct=function(v){return (Number(v)>=0?"+":"")+fmt(v,2)+"%"};
  var cls=function(v){return Number(v)>0?"up":Number(v)<0?"down":"dim"};
  var keys=Object.keys(sum);
  var tbars=keys.map(function(k){
    var v=sum[k];
    return {label:k,value:v.tStat,
      color:Math.abs(v.tStat)>=2?(v.tStat>0?"#2ebd85":"#f6465d"):"#5f6d8c",
      tip:'<div class="r"><span>样本</span><b>'+v.n+'</b></div><div class="r"><span>均值</span><b>'+pct(v.meanPct)
        +'</b></div><div class="r"><span>胜率</span><b>'+(v.winRate*100).toFixed(0)+"%</b></div>"};
  });
  var retBars=runs.filter(function(x){return !x.error}).map(function(x){
    return {label:x.symbol+" "+x.interval+" · "+x.strategy,value:x.returnPct,
      tip:'<div class="r"><span>标的涨跌</span><b>'+pct(x.benchmarkPct)+'</b></div><div class="r"><span>最大回撤</span><b>'
        +fmt(x.maxDrawdownPct,2)+"%</b></div>"};
  });
  var sumRows=keys.map(function(k){
    var v=sum[k];
    return "<tr><td><b>"+esc(k)+'</b></td><td class="r num">'+v.n
      +'</td><td class="r num '+cls(v.meanPct)+'">'+pct(v.meanPct)
      +'</td><td class="r num '+cls(v.medianPct)+'">'+pct(v.medianPct)
      +'</td><td class="r num">'+fmt(v.sdPct,2)+'%</td><td class="r num">'+(v.winRate*100).toFixed(0)
      +'%</td><td class="r num '+(Math.abs(v.tStat)>=2?cls(v.tStat):"dim")+'">'+fmt(v.tStat,2)
      +'</td><td class="r num">'+fmt(v.meanFeePctOfEquity,2)+"%</td></tr>";
  }).join("");
  var runRows=runs.map(function(x){
    if(x.error){
      return "<tr><td>"+esc(x.symbol)+" "+esc(x.interval)+"</td><td>"+esc(x.strategy)
        +'</td><td colspan="7" class="down">'+esc(x.error)+"</td></tr>";
    }
    var tag="grid";
    var agree=x.llmAgreement&&typeof x.llmAgreement.rate==="number"
      ?' <span class="dim" title="规则代理与真 LLM 的决策一致率">一致率 '+(x.llmAgreement.rate*100).toFixed(0)+"%</span>":"";
    return "<tr><td><b>"+esc(x.symbol)+"</b> "+esc(x.interval)+'</td><td><span class="tag '+tag+'">'+esc(x.strategy)+"</span>"+agree+"</td>"
      +'<td class="r num">'+fmt(x.days,0)+'</td><td class="r num '+cls(x.returnPct)+'">'+pct(x.returnPct)
      +'</td><td class="r num down">'+fmt(x.maxDrawdownPct,2)+'%</td><td class="r num dim">'+pct(x.benchmarkPct)
      +'</td><td class="r num">'+fmt(x.fees.pctOfInitial,2)+'%</td><td class="r num">'+(x.fills.takerRatio*100).toFixed(0)
      +'%</td><td style="width:130px">'+sparkline((x.equityCurve||[]).map(function(e){return e.equity}),{h:24})+"</td></tr>";
  }).join("");
  var age=Math.round((Date.now()-Date.parse(r.generatedAt))/60000);
  return '<div class="g2">'
    +'<div class="panel"><h3>统计显著性 <span class="hint">|t| &lt; 2（灰色）表示平均收益与零在统计上无法区分</span></h3>'
      +chartBarsH(tbars,{name:"t 值",dec:2,labelWidth:200})+"</div>"
    +'<div class="panel"><h3>逐样本收益</h3>'+chartBarsH(retBars,{name:"收益",dec:2,unit:"%",labelWidth:230})+"</div>"
    +"</div>"
    +'<div class="panel"><h3>策略汇总</h3><div class="scroll"><table><tr><th>策略</th><th class="r">样本数</th><th class="r">均值</th>'
      +'<th class="r">中位数</th><th class="r">标准差</th><th class="r">胜率</th><th class="r">t 值</th><th class="r">平均费用</th></tr>'
      +sumRows+"</table></div></div>"
    +'<div class="panel"><h3>逐样本明细</h3><div class="scroll"><table><tr><th>样本</th><th>策略</th><th class="r">天数</th>'
      +'<th class="r">收益</th><th class="r">最大回撤</th><th class="r">标的涨跌</th><th class="r">费用</th><th class="r">taker</th>'
      +"<th>净值曲线</th></tr>"+runRows+"</table></div></div>"
    +'<div class="panel"><h3>报告信息</h3><div class="dim">生成于 '+esc(new Date(r.generatedAt).toLocaleString("zh-CN"))
      +"（"+age+" 分钟前）· 耗时 "+Math.round((r.elapsedMs||0)/1000)+"s · 初始权益 "+usd((r.params||{}).initialEquity)
      +"<br>回测撮合偏保守，数字用于比较方案而非预测收益。"
      +"</div></div>";
}

/* ── 配置 ───────────────────────────────────────────────────────── */
function ref(schema,id){return schema.refs[String(id)]}
/** 字段控件：schema 显式传入，不依赖任何全局状态（union 类型要解引用 refs）。 */
function fieldInput(schema,path,node,value){
  var t=node.type,m=node.meta||{};
  var cur=value!==undefined?value:m.default;
  if(t==="boolean"){
    return '<select data-path="'+path+'" data-type="boolean"><option value="true"'+(cur===true?" selected":"")
      +'>开启</option><option value="false"'+(cur===false?" selected":"")+">关闭</option></select>";
  }
  if(t==="union"){
    return '<select data-path="'+path+'" data-type="union">'+node.list.map(function(id){
      var c=ref(schema,id);
      return '<option value="'+esc(c.value)+'"'+(cur===c.value?" selected":"")+">"+esc(c.value)
        +((c.meta||{}).description?"（"+esc(c.meta.description)+"）":"")+"</option>";
    }).join("")+"</select>";
  }
  if(t==="number"){
    return '<input type="number" data-path="'+path+'" data-type="number"'+(m.min!=null?' min="'+m.min+'"':"")
      +(m.max!=null?' max="'+m.max+'"':"")+(m.step!=null?' step="'+m.step+'"':' step="any"')+' value="'+esc(cur)+'">';
  }
  if(t==="array"){
    return '<input type="text" data-path="'+path+'" data-type="array" value="'
      +esc(Array.isArray(cur)?cur.join(", "):"")+'" placeholder="逗号分隔">';
  }
  if(t==="dict"){
    return '<textarea data-path="'+path+'" data-type="json" rows="3" placeholder="JSON 对象">'
      +esc(JSON.stringify(cur==null?{}:cur,null,1))+"</textarea>";
  }
  return '<input type="text" data-path="'+path+'" data-type="string" value="'+esc(cur==null?"":cur)+'">';
}
function viewConfig(){
  return api("/api/config").then(function(cfg){
    return globalScopeHeader("配置","表单由 Schema 自动生成 · 保存后引擎优雅完成当前周期再热重配",
      ['<span class="badge warn">保存会重启全部账户的策略循环</span>'])+configHtml(cfg);
  });
}
function accountsPreview(list){
  if(!list||!list.length){
    return '<div class="dim" style="margin-bottom:8px">当前为<b>单账户模式</b>：走顶层配置与 HYPERLIQUID_* 环境变量，'
      +"账户名固定为 default。下方数组填入条目即可切换为多账户并行。</div>";
  }
  return '<div class="cards" style="margin-bottom:10px">'+list.map(function(a,i){
    var color=ACOLORS[i%ACOLORS.length];
    var t=a.trading||{};
    return '<div class="card" style="--ac:'+color+';border-left:4px solid '+color+'">'
      +'<div class="k"><span class="avatar sm" style="background:'+color+'">'+esc(initials(a.name))+"</span>"+esc(a.name)+"</div>"
      +'<div class="s" style="margin-top:5px">'+envBadge(a.testnet!==false)+"</div>"
      +'<div class="s mono">'+esc(a.private_key_env||"HYPERLIQUID_PRIVATE_KEY")+"</div>"
      +'<div class="s">'+(t.grid_enabled!==undefined||t.symbols!==undefined
        ?stratTags({grid:t.grid_enabled!==false,grid_symbol:(t.symbols||[])[0]})
        :'<span class="dim2">继承顶层策略</span>')+"</div></div>";
  }).join("")+"</div>";
}
window.addAccountTemplate=function(){
  var ta=document.querySelector('[data-path="accounts"]');
  if(!ta)return;
  var cur;
  try{cur=JSON.parse(ta.value||"[]")}catch(e){toast("❌ accounts 当前不是合法 JSON，先修好再添加");return}
  if(!Array.isArray(cur))cur=[];
  cur.push({name:"account-"+(cur.length+1),private_key_env:"HYPERLIQUID_PRIVATE_KEY_2",testnet:true,
    trading:{grid_enabled:true,symbols:["BTC"]}});
  ta.value=JSON.stringify(cur,null,1);
  toast("已追加一条账户模板，记得改 private_key_env 再保存");
};
function configHtml(cfg){
  var schema=cfg.schema,root=ref(schema,schema.uid);
  var ov=cfg.overrides||{},base=cfg.base||{};
  var html='<div class="toolbar"><button class="btn" onclick="saveConfig()">💾 保存并热应用</button>'
    +'<button class="btn ghost" onclick="resetConfig()">↩︎ 清空覆盖，回到基线</button>'
    +'<span class="dim">● = 看板覆盖项（叠加在 cordis.yml 基线之上）。私钥与 API Key 只能通过环境变量配置，'
    +"看板永远读不到也写不了。</span></div>";
  var sections=Object.keys(root.dict);
  html+='<div class="panel" style="padding:9px 12px"><div class="legend">'+sections.map(function(s){
    return '<span class="li"><a href="#sec-'+esc(s)+'" style="color:var(--dim);text-decoration:none">'+esc(s)+"</a></span>";
  }).join("")+"</div></div>";
  sections.forEach(function(section){
    var node=ref(schema,root.dict[section]);
    var desc=(node.meta||{}).description||"";
    if(section==="accounts"){
      var cur=(ov.accounts!==undefined?ov.accounts:base.accounts)||[];
      html+='<div class="panel" id="sec-accounts"><h3>accounts · 多账户并行'
        +'<span class="hint">每条目 = 一套「地址 × 环境 × 策略」，各自独立引擎/状态/日志/保护链</span>'
        +'<span style="flex:1"></span><button class="btn ghost" onclick="addAccountTemplate()">+ 添加账户模板</button></h3>'
        +accountsPreview(cur)
        +'<div class="field'+(ov.accounts!==undefined?" override":"")+'"><label>accounts</label>'
        +'<textarea data-path="accounts" data-type="json" rows="10">'+esc(JSON.stringify(cur,null,1))+"</textarea>"
        +'<span class="desc">'+esc(desc)+" 私钥永远只写<b>环境变量名</b>。保存后大盘整体热重配。</span></div></div>";
      return;
    }
    if(section==="protections"){
      var cp=(ov.protections!==undefined?ov.protections:base.protections);
      html+='<div class="panel" id="sec-protections"><h3>protections · 账户保护链'
        +'<span class="hint">null=默认链 / []=全关（不建议）/ 自定义数组</span></h3>'
        +'<div class="field'+(ov.protections!==undefined?" override":"")+'"><label>protections</label>'
        +'<textarea data-path="protections" data-type="json" rows="6">'
        +esc(JSON.stringify(cp===undefined?null:cp,null,1))+"</textarea>"
        +'<span class="desc">'+esc(desc)+"</span></div></div>";
      return;
    }
    if(node.type!=="object")return;
    html+='<div class="panel" id="sec-'+esc(section)+'"><h3>'+esc(section)
      +'<span class="hint">'+esc(desc)+"</span></h3>";
    Object.keys(node.dict).forEach(function(key){
      var f=ref(schema,node.dict[key]);
      var path=section+"."+key;
      var ovHas=ov[section]&&Object.prototype.hasOwnProperty.call(ov[section],key);
      var val=ovHas?ov[section][key]:(base[section]||{})[key];
      html+='<div class="field'+(ovHas?" override":"")+'"><label>'+esc(key)+"</label><div>"
        +fieldInput(schema,path,f,val)+'</div><span class="desc">'+esc((f.meta||{}).description||"")+"</span></div>";
    });
    html+="</div>";
  });
  return html;
}
window.saveConfig=function(){
  var overrides={},bad=null;
  Array.prototype.forEach.call(document.querySelectorAll("[data-path]"),function(el){
    var path=el.dataset.path,type=el.dataset.type,v=el.value;
    try{
      if(type==="boolean")v=v==="true";
      else if(type==="number"){if(v==="")return;v=Number(v);if(!isFinite(v))throw new Error("非法数字")}
      else if(type==="array")v=v.split(",").map(function(s){return s.trim()}).filter(Boolean);
      else if(type==="json")v=(v.trim()===""||v.trim()==="null")?null:JSON.parse(v);
    }catch(e){bad=path+": "+e.message;return}
    if(path==="protections"){overrides.protections=v;return}
    if(path==="accounts"){overrides.accounts=v===null?[]:v;return}
    var parts=path.split(".");
    if(!overrides[parts[0]])overrides[parts[0]]={};
    overrides[parts[0]][parts[1]]=v;
  });
  if(bad){toast("❌ "+esc(bad));return}
  api("/api/config",{method:"PUT",body:JSON.stringify({overrides:overrides})}).then(function(){
    toast("✅ 配置已保存并热应用");
    return api("/api/accounts").then(function(list){ROSTER=list||[];renderSide()});
  }).then(function(){setTimeout(render,600)}).catch(function(e){toast("❌ "+esc(e.message),8000)});
};
window.resetConfig=function(){
  if(!confirm("确定清空所有看板覆盖项、回到 cordis.yml 基线配置？"))return;
  api("/api/config/reset",{method:"POST"}).then(function(){
    toast("✅ 已回到基线配置");
    return api("/api/accounts").then(function(list){ROSTER=list||[];renderSide()});
  }).then(function(){setTimeout(render,600)}).catch(function(e){toast("❌ "+esc(e.message),8000)});
};
`;
