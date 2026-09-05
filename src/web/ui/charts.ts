/**
 * 看板图表工具（纯 SVG 字符串，零依赖、零外部资源）。
 *
 * 全部为**纯函数**：吃数据、吐 HTML 字符串，不碰 DOM——因此可以在 vitest 里
 * 直接断言输出（见 tests/webUi.test.ts）。交互只用两样东西：
 * ① CSS `:hover` 控制十字线与数据点的显隐（无需 JS）；
 * ② `data-tip="<索引>"` 指向全局 TIPS 数组，由 shell 的委托监听器渲染浮层——
 *    用索引而不是把 HTML 塞进属性，省掉一整类转义问题。
 */

export const CHARTS_JS = String.raw`
/* ── 数值与时间格式化 ────────────────────────────────────────────── */
function cfmt(v,d){
  var n=Number(v);
  if(!isFinite(n))return "-";
  var a=Math.abs(n);
  if(a>=1e9)return (n/1e9).toFixed(2)+"B";
  if(a>=1e6)return (n/1e6).toFixed(2)+"M";
  if(a>=1e4)return (n/1e3).toFixed(1)+"k";
  return n.toFixed(d==null?2:d);
}
function tlab(ms){
  var d=new Date(ms);
  if(isNaN(d.getTime()))return "";
  var p=function(x){return (x<10?"0":"")+x};
  return p(d.getMonth()+1)+"-"+p(d.getDate())+" "+p(d.getHours())+":"+p(d.getMinutes());
}
function tfull(ms){
  var d=new Date(ms);
  return isNaN(d.getTime())?"":d.toLocaleString("zh-CN",{hour12:false});
}
/** 把任意时间字段（ISO 串 / 毫秒 / 秒）归一为毫秒；失败返回 NaN。 */
function tms(v){
  if(v==null)return NaN;
  if(typeof v==="number")return v<1e11?v*1000:v;
  var n=Date.parse(v);
  return isNaN(n)?NaN:n;
}
/** 坐标轴刻度：把区间切成人眼友好的整数步长。 */
function ticks(min,max,count){
  if(!(isFinite(min)&&isFinite(max)))return [0];
  if(min===max)return [min];
  var span=(max-min)/Math.max(1,count);
  var mag=Math.pow(10,Math.floor(Math.log(span)/Math.LN10));
  var norm=span/mag,step=mag;
  if(norm>5)step=10*mag;else if(norm>2)step=5*mag;else if(norm>1)step=2*mag;
  var out=[],v=Math.ceil(min/step)*step;
  for(var i=0;i<40&&v<=max+step*0.001;i++,v+=step)out.push(v);
  return out.length?out:[min,max];
}
function svgOpen(h,cls){
  return '<svg class="chart '+(cls||"")+'" viewBox="0 0 1000 '+h+'" preserveAspectRatio="xMidYMid meet" role="img">';
}

/* ── 折线 / 面积图（支持多序列对比与十字线浮层）───────────────── */
/**
 * o = {series:[{name,color,pts:[[毫秒,值],…]}], h, unit, area, pct, zero}
 * pts 必须按时间升序。多序列时按并集时间轴重采样（前值填充），
 * 于是不同账户的净值快照即使采样时刻不同也能同框对比。
 */
function chartLine(o){
  var series=(o.series||[]).filter(function(s){return s.pts&&s.pts.length});
  var h=o.h||260,W=1000,L=62,R=16,T=14,B=26;
  if(!series.length)return '<div class="empty">'+(o.emptyText||"暂无数据")+"</div>";
  var tmin=Infinity,tmax=-Infinity,vmin=Infinity,vmax=-Infinity;
  series.forEach(function(s){
    s.pts.forEach(function(p){
      if(p[0]<tmin)tmin=p[0];if(p[0]>tmax)tmax=p[0];
      if(p[1]<vmin)vmin=p[1];if(p[1]>vmax)vmax=p[1];
    });
  });
  if(o.zero){if(vmin>0)vmin=0;if(vmax<0)vmax=0}
  if(!isFinite(tmin)||!isFinite(vmin))return '<div class="empty">'+(o.emptyText||"暂无数据")+"</div>";
  if(tmax===tmin)tmax=tmin+1;
  var padv=(vmax-vmin)*0.08||Math.abs(vmax||1)*0.05||1;
  var lo=vmin-padv,hi=vmax+padv;
  var X=function(t){return L+(t-tmin)/(tmax-tmin)*(W-L-R)};
  var Y=function(v){return h-B-(v-lo)/(hi-lo)*(h-T-B)};
  var out=svgOpen(h);
  var uid="g"+(CHART_SEQ++);
  // 网格线与 Y 轴刻度
  ticks(lo,hi,4).forEach(function(v){
    var y=Y(v).toFixed(1);
    out+='<line class="gridline" x1="'+L+'" y1="'+y+'" x2="'+(W-R)+'" y2="'+y+'"/>'
      +'<text x="'+(L-8)+'" y="'+(Number(y)+3.5)+'" text-anchor="end">'+esc(cfmt(v,o.dec==null?2:o.dec))+(o.unit||"")+"</text>";
  });
  // X 轴时间刻度
  for(var i=0;i<=3;i++){
    var t=tmin+(tmax-tmin)*i/3,x=X(t);
    out+='<text x="'+x.toFixed(1)+'" y="'+(h-8)+'" text-anchor="'+(i===0?"start":i===3?"end":"middle")+'">'+esc(tlab(t))+"</text>";
  }
  out+='<line class="axis" x1="'+L+'" y1="'+(h-B)+'" x2="'+(W-R)+'" y2="'+(h-B)+'"/>';
  if(lo<0&&hi>0)out+='<line class="axis" x1="'+L+'" y1="'+Y(0).toFixed(1)+'" x2="'+(W-R)+'" y2="'+Y(0).toFixed(1)+'" stroke-dasharray="4 3"/>';
  // 序列
  series.forEach(function(s,si){
    var d=s.pts.map(function(p,i){return (i?"L":"M")+X(p[0]).toFixed(1)+" "+Y(p[1]).toFixed(1)}).join(" ");
    if(o.area&&si===0){
      out+='<defs><linearGradient id="'+uid+"_"+si+'" x1="0" y1="0" x2="0" y2="1">'
        +'<stop offset="0%" stop-color="'+s.color+'" stop-opacity=".28"/>'
        +'<stop offset="100%" stop-color="'+s.color+'" stop-opacity="0"/></linearGradient></defs>'
        +'<path d="'+d+" L"+X(s.pts[s.pts.length-1][0]).toFixed(1)+" "+(h-B)+" L"+X(s.pts[0][0]).toFixed(1)+" "+(h-B)+' Z" fill="url(#'+uid+"_"+si+')" stroke="none"/>';
    }
    out+='<path d="'+d+'" fill="none" stroke="'+s.color+'" stroke-width="'+(o.thin?1.3:1.8)+'" stroke-linejoin="round" stroke-linecap="round"/>';
  });
  // 十字线热区：按并集时间轴重采样，最多 140 列
  var axis=[];
  series.forEach(function(s){s.pts.forEach(function(p){axis.push(p[0])})});
  axis.sort(function(a,b){return a-b});
  var uniq=[];
  for(var k=0;k<axis.length;k++)if(!k||axis[k]!==axis[k-1])uniq.push(axis[k]);
  var stride=Math.max(1,Math.ceil(uniq.length/140));
  var ptr=series.map(function(){return 0});
  var bw=(W-L-R)/Math.max(1,Math.ceil(uniq.length/stride));
  for(var j=0;j<uniq.length;j+=stride){
    var t2=uniq[j],x2=X(t2),rows="",dots="";
    series.forEach(function(s,si){
      while(ptr[si]+1<s.pts.length&&s.pts[ptr[si]+1][0]<=t2)ptr[si]++;
      var p=s.pts[ptr[si]];
      if(!p||p[0]>t2)return;
      rows+='<div class="r"><span><span class="sw" style="background:'+s.color+'"></span>'+esc(s.name)+"</span><b>"
        +esc(cfmt(p[1],o.dec==null?2:o.dec))+(o.unit||"")+"</b></div>";
      dots+='<circle class="hd" cx="'+x2.toFixed(1)+'" cy="'+Y(p[1]).toFixed(1)+'" r="3.2" fill="'+s.color+'" stroke="#0b0f1a" stroke-width="1.2"/>';
    });
    out+='<g class="hbg" data-tip="'+tipId('<div class="t">'+esc(tfull(t2))+"</div>"+rows)+'">'
      +'<rect class="hb" x="'+(x2-bw/2).toFixed(1)+'" y="'+T+'" width="'+bw.toFixed(1)+'" height="'+(h-T-B)+'"/>'
      +'<line class="hx" x1="'+x2.toFixed(1)+'" y1="'+T+'" x2="'+x2.toFixed(1)+'" y2="'+(h-B)+'"/>'+dots+"</g>";
  }
  out+="</svg>";
  if(o.legend!==false&&series.length>1){
    out+='<div class="legend">'+series.map(function(s){
      var last=s.pts[s.pts.length-1][1];
      return '<span class="li"><span class="sw" style="background:'+s.color+'"></span>'+esc(s.name)
        +' <b class="num">'+esc(cfmt(last,o.dec==null?2:o.dec))+(o.unit||"")+"</b></span>";
    }).join("")+"</div>";
  }
  return out;
}

/* ── 横向条形图（账户间对比：正负分色，账户身份色作标识点）───── */
function chartBarsH(items,o){
  o=o||{};
  var rows=(items||[]).filter(function(x){return x&&isFinite(Number(x.value))});
  if(!rows.length)return '<div class="empty">'+(o.emptyText||"暂无数据")+"</div>";
  var rh=o.rowHeight||30,h=rows.length*rh+16,W=1000,L=o.labelWidth||160,R=90;
  var max=0;
  rows.forEach(function(r){max=Math.max(max,Math.abs(Number(r.value)))});
  if(!max)max=1;
  var neg=rows.some(function(r){return Number(r.value)<0});
  var zero=neg?L+(W-L-R)/2:L;
  var span=neg?(W-L-R)/2:(W-L-R);
  var out=svgOpen(h);
  out+='<line class="axis" x1="'+zero+'" y1="6" x2="'+zero+'" y2="'+(h-8)+'"/>';
  rows.forEach(function(r,i){
    var v=Number(r.value),y=10+i*rh,w=Math.abs(v)/max*span*0.92;
    var color=r.color||(v>=0?"#2ebd85":"#f6465d");
    var x=v>=0?zero:zero-w;
    out+='<g class="hbg" data-tip="'+tipId('<div class="t">'+esc(r.label)+"</div>"+'<div class="r"><span>'
        +esc(o.name||"数值")+"</span><b>"+esc(cfmt(v,o.dec==null?4:o.dec))+(o.unit||"")+"</b></div>"+(r.tip||""))+'">'
      +'<rect class="hb" x="0" y="'+y+'" width="'+W+'" height="'+(rh-4)+'"/>'
      +'<rect x="'+x.toFixed(1)+'" y="'+(y+4)+'" width="'+Math.max(1.5,w).toFixed(1)+'" height="'+(rh-14)+'" rx="3" fill="'+color+'" opacity=".85"/>'
      +'<text x="'+(L-10)+'" y="'+(y+rh/2)+'" text-anchor="end" dominant-baseline="middle" style="fill:var(--text)">'+esc(r.label)+"</text>"
      +(r.dotColor?'<rect x="'+(L-6)+'" y="'+(y+rh/2-4)+'" width="4" height="8" rx="2" fill="'+r.dotColor+'"/>':"")
      +'<text x="'+(W-8)+'" y="'+(y+rh/2)+'" text-anchor="end" dominant-baseline="middle" style="fill:'
        +(v>=0?"var(--up)":"var(--down)")+'">'+esc((v>0?"+":"")+cfmt(v,o.dec==null?4:o.dec))+(o.unit||"")+"</text></g>";
  });
  return out+"</svg>";
}

/* ── 环形图（净值构成）──────────────────────────────────────────── */
function chartDonut(items,o){
  o=o||{};
  var rows=(items||[]).filter(function(x){return Number(x.value)>0});
  var total=rows.reduce(function(a,b){return a+Number(b.value)},0);
  if(!total)return '<div class="empty">'+(o.emptyText||"暂无数据")+"</div>";
  var h=o.h||230,cx=500,cy=h/2,r=Math.min(h/2-12,96),ir=r*0.62,ang=-Math.PI/2,out=svgOpen(h);
  rows.forEach(function(row){
    var frac=Number(row.value)/total,a2=ang+frac*Math.PI*2;
    var big=frac>0.5?1:0;
    var p=function(a,rad){return [(cx+Math.cos(a)*rad).toFixed(2),(cy+Math.sin(a)*rad).toFixed(2)]};
    var s1=p(ang,r),e1=p(a2,r),s2=p(a2,ir),e2=p(ang,ir);
    // 单账户占比 100% 时圆弧退化，直接画整环
    var d=frac>0.999
      ?"M"+(cx-r)+" "+cy+"A"+r+" "+r+" 0 1 1 "+(cx+r)+" "+cy+"A"+r+" "+r+" 0 1 1 "+(cx-r)+" "+cy
        +"M"+(cx-ir)+" "+cy+"A"+ir+" "+ir+" 0 1 0 "+(cx+ir)+" "+cy+"A"+ir+" "+ir+" 0 1 0 "+(cx-ir)+" "+cy
      :"M"+s1[0]+" "+s1[1]+"A"+r+" "+r+" 0 "+big+" 1 "+e1[0]+" "+e1[1]
        +"L"+s2[0]+" "+s2[1]+"A"+ir+" "+ir+" 0 "+big+" 0 "+e2[0]+" "+e2[1]+"Z";
    out+='<g class="hbg" data-tip="'+tipId('<div class="t">'+esc(row.label)+"</div>"+'<div class="r"><span>'
        +esc(o.name||"净值")+"</span><b>"+esc(cfmt(row.value))+"</b></div>"+'<div class="r"><span>占比</span><b>'
        +(frac*100).toFixed(1)+"%</b></div>")+'">'
      +'<path d="'+d+'" fill="'+(row.color||"#4d6bfe")+'" opacity=".88" fill-rule="evenodd"/></g>';
    ang=a2;
  });
  out+='<text x="'+cx+'" y="'+(cy-4)+'" text-anchor="middle" style="fill:var(--text);font-size:19px;font-weight:600">'
    +esc(cfmt(total))+"</text>"
    +'<text x="'+cx+'" y="'+(cy+15)+'" text-anchor="middle">'+esc(o.centerLabel||"合计")+"</text></svg>";
  out+='<div class="legend">'+rows.map(function(row){
    return '<span class="li"><span class="sw" style="background:'+(row.color||"#4d6bfe")+'"></span>'+esc(row.label)
      +' <b class="num">'+(Number(row.value)/total*100).toFixed(1)+"%</b></span>";
  }).join("")+"</div>";
  return out;
}

/* ── 竖向条形图（分类计数：决策类型分布等）─────────────────────── */
function chartBarsV(items,o){
  o=o||{};
  var rows=(items||[]).filter(Boolean);
  if(!rows.length)return '<div class="empty">'+(o.emptyText||"暂无数据")+"</div>";
  var h=o.h||190,W=1000,L=46,R=12,T=12,B=34;
  var max=0;rows.forEach(function(r){max=Math.max(max,Number(r.value)||0)});
  if(!max)max=1;
  var bw=(W-L-R)/rows.length;
  var out=svgOpen(h);
  ticks(0,max,3).forEach(function(v){
    var y=(h-B-v/max*(h-T-B)).toFixed(1);
    out+='<line class="gridline" x1="'+L+'" y1="'+y+'" x2="'+(W-R)+'" y2="'+y+'"/>'
      +'<text x="'+(L-8)+'" y="'+(Number(y)+3.5)+'" text-anchor="end">'+esc(cfmt(v,0))+"</text>";
  });
  rows.forEach(function(r,i){
    var v=Number(r.value)||0,bh=v/max*(h-T-B),x=L+i*bw;
    out+='<g class="hbg" data-tip="'+tipId('<div class="t">'+esc(r.label)+'</div><div class="r"><span>'
        +esc(o.name||"数量")+"</span><b>"+v+"</b></div>"+(r.tip||""))+'">'
      +'<rect class="hb" x="'+x.toFixed(1)+'" y="'+T+'" width="'+bw.toFixed(1)+'" height="'+(h-T-B)+'"/>'
      +'<rect x="'+(x+bw*0.18).toFixed(1)+'" y="'+(h-B-bh).toFixed(1)+'" width="'+(bw*0.64).toFixed(1)
      +'" height="'+Math.max(1,bh).toFixed(1)+'" rx="3" fill="'+(r.color||"#4d6bfe")+'" opacity=".85"/>'
      +'<text x="'+(x+bw/2).toFixed(1)+'" y="'+(h-B-bh-5).toFixed(1)+'" text-anchor="middle" style="fill:var(--text)">'+v+"</text>"
      +'<text x="'+(x+bw/2).toFixed(1)+'" y="'+(h-14)+'" text-anchor="middle">'+esc(r.label)+"</text></g>";
  });
  return out+"</svg>";
}

/* ── 网格层级阶梯图（价格轴上的层级实况）───────────────────────── */
/**
 * 网格看的是「层级相对现价的分布 + 每层处在状态机的哪一格」，表格读不出这个形状。
 * o = {levels, current, lower, upper, dec}
 */
function chartLadder(o){
  var lv=(o.levels||[]).slice().sort(function(a,b){return Number(b.price)-Number(a.price)});
  if(!lv.length)return '<div class="empty">暂无层级（等待 UPDATE_GRID 建网）</div>';
  var cur=Number(o.current)||0,dec=o.dec==null?4:o.dec;
  var prices=lv.map(function(l){return Number(l.price)});
  if(cur>0)prices.push(cur);
  if(Number(o.lower)>0)prices.push(Number(o.lower));
  if(Number(o.upper)>0)prices.push(Number(o.upper));
  var lo=Math.min.apply(null,prices),hi=Math.max.apply(null,prices);
  var pad=(hi-lo)*0.06||hi*0.005||1;
  lo-=pad;hi+=pad;
  var h=Math.max(240,lv.length*17+70),W=1000,L=96,R=250,T=16,B=16;
  var Y=function(p){return h-B-(p-lo)/(hi-lo)*(h-T-B)};
  var stateCn={IDLE:"空闲",OPEN_PENDING:"开仓挂单",OPEN_FILLED:"已开仓",CLOSE_PENDING:"平仓挂单",COMPLETED:"已完成"};
  var out=svgOpen(h);
  if(Number(o.lower)>0&&Number(o.upper)>0){
    var yTop=Y(Number(o.upper)),yBot=Y(Number(o.lower));
    out+='<rect x="'+L+'" y="'+yTop.toFixed(1)+'" width="'+(W-L-R)+'" height="'+Math.max(1,yBot-yTop).toFixed(1)
      +'" fill="#4d6bfe" opacity=".05"/>'
      +'<text x="'+(W-R+8)+'" y="'+(yTop+11).toFixed(1)+'" style="fill:var(--dim2)">区间上沿 '+esc(cfmt(o.upper,dec))+"</text>"
      +'<text x="'+(W-R+8)+'" y="'+(yBot-4).toFixed(1)+'" style="fill:var(--dim2)">区间下沿 '+esc(cfmt(o.lower,dec))+"</text>";
  }
  lv.forEach(function(l){
    var p=Number(l.price),y=Y(p),long=String(l.side).toUpperCase()==="LONG";
    var base=long?"#2ebd85":"#f6465d",st=String(l.state).toUpperCase();
    var op=st==="OPEN_FILLED"?1:st==="CLOSE_PENDING"?0.8:st==="OPEN_PENDING"?0.6:st==="COMPLETED"?0.45:0.3;
    var dash=(st==="OPEN_PENDING"||st==="CLOSE_PENDING")?' stroke-dasharray="5 4"':"";
    var pnl=Number(l.cumulative_pnl)||0;
    out+='<g class="hbg" data-tip="'+tipId('<div class="t">'+esc(l.id)+" · "+(long?"多":"空")+"</div>"
        +'<div class="r"><span>状态</span><b>'+esc(stateCn[st]||st)+"</b></div>"
        +'<div class="r"><span>格价</span><b>'+esc(cfmt(p,dec))+"</b></div>"
        +'<div class="r"><span>开仓成交</span><b>'+esc(l.open_fill_price?cfmt(l.open_fill_price,dec):"—")+"</b></div>"
        +'<div class="r"><span>轮次</span><b>'+(l.round_trip_count||0)+"</b></div>"
        +'<div class="r"><span>累计盈亏</span><b>'+esc(cfmt(pnl,4))+"</b></div>")+'">'
      +'<rect class="hb" x="0" y="'+(y-7).toFixed(1)+'" width="'+W+'" height="14"/>'
      +'<line x1="'+L+'" y1="'+y.toFixed(1)+'" x2="'+(W-R)+'" y2="'+y.toFixed(1)+'" stroke="'+base+'" stroke-width="2" opacity="'+op+'"'+dash+"/>"
      +'<text x="'+(L-8)+'" y="'+(y+3.5).toFixed(1)+'" text-anchor="end" class="num">'+esc(cfmt(p,dec))+"</text>";
    if(st==="OPEN_FILLED"||st==="CLOSE_PENDING"){
      var fx=l.open_fill_price?Y(Number(l.open_fill_price)):y;
      out+='<circle cx="'+(L+14)+'" cy="'+fx.toFixed(1)+'" r="3.6" fill="'+base+'"/>';
    }
    out+='<text x="'+(W-R+8)+'" y="'+(y+3.5).toFixed(1)+'" style="fill:var(--dim)">'
      +esc(l.id)+" · "+esc(stateCn[st]||st)+"</text>";
    if(pnl){
      out+='<text x="'+(W-8)+'" y="'+(y+3.5).toFixed(1)+'" text-anchor="end" style="fill:'
        +(pnl>0?"var(--up)":"var(--down)")+'">'+esc((pnl>0?"+":"")+cfmt(pnl,4))+"</text>";
    }
    out+="</g>";
  });
  if(cur>0){
    var yc=Y(cur);
    out+='<line x1="'+L+'" y1="'+yc.toFixed(1)+'" x2="'+(W-R)+'" y2="'+yc.toFixed(1)+'" stroke="#f0b90b" stroke-width="1.6"/>'
      +'<rect x="'+(L-90)+'" y="'+(yc-10).toFixed(1)+'" width="84" height="20" rx="5" fill="#f0b90b"/>'
      +'<text x="'+(L-48)+'" y="'+(yc+4).toFixed(1)+'" text-anchor="middle" style="fill:#0b0f1a;font-weight:700">'
      +esc(cfmt(cur,dec))+"</text>";
  }
  return out+"</svg>";
}

/* ── 迷你走势线（表格与侧栏内嵌）────────────────────────────────── */
function sparkline(vals,o){
  o=o||{};
  var v=(vals||[]).map(Number).filter(function(x){return isFinite(x)});
  var w=o.w||120,h=o.h||26;
  if(v.length<2)return '<svg class="chart" viewBox="0 0 '+w+" "+h+'" style="width:'+w+'px;height:'+h+'px"></svg>';
  var min=Math.min.apply(null,v),max=Math.max.apply(null,v),span=(max-min)||1;
  var pts=v.map(function(x,i){return (i/(v.length-1)*w).toFixed(1)+","+(h-2-(x-min)/span*(h-4)).toFixed(1)}).join(" ");
  var color=o.color||(v[v.length-1]>=v[0]?"#2ebd85":"#f6465d");
  return '<svg class="chart" viewBox="0 0 '+w+" "+h+'" preserveAspectRatio="none" style="width:100%;height:'+h+'px">'
    +'<polyline fill="none" stroke="'+color+'" stroke-width="1.5" stroke-linejoin="round" points="'+pts+'"/></svg>';
}

/* ── 单笔盈亏直方图 ─────────────────────────────────────────────── */
function chartHist(values,o){
  o=o||{};
  var v=(values||[]).map(Number).filter(function(x){return isFinite(x)});
  if(v.length<2)return '<div class="empty">样本不足</div>';
  var min=Math.min.apply(null,v),max=Math.max.apply(null,v);
  if(min===max){min-=1;max+=1}
  var n=Math.min(24,Math.max(6,Math.round(Math.sqrt(v.length))));
  var step=(max-min)/n,bins=[];
  for(var i=0;i<n;i++)bins.push({lo:min+i*step,hi:min+(i+1)*step,c:0});
  v.forEach(function(x){
    var k=Math.min(n-1,Math.floor((x-min)/step));
    bins[k].c++;
  });
  return chartBarsV(bins.map(function(b){
    return {label:cfmt((b.lo+b.hi)/2,o.dec==null?3:o.dec),value:b.c,
      color:(b.lo+b.hi)/2>=0?"#2ebd85":"#f6465d",
      tip:'<div class="r"><span>区间</span><b>'+cfmt(b.lo,4)+" ~ "+cfmt(b.hi,4)+"</b></div>"};
  }),{h:o.h||180,name:"笔数"});
}
`;
