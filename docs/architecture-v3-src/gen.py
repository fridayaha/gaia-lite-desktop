#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble docs/architecture-v3.html — runtime mermaid (inlined) + fixed zoom lightbox."""
import os, html

_HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(_HERE, "src")
MERMAID_LIB = os.path.join(_HERE, "mermaid.min.js")
TARGET = os.path.join(_HERE, "..", "architecture-v3.html")

def mmd(name):
    with open(os.path.join(SRC, name + ".mmd"), encoding="utf-8") as f:
        return f.read().strip()

with open(MERMAID_LIB, encoding="utf-8") as f:
    MERMAID_JS = f.read()

CSS = r"""
:root{
  --bg:#f6f7fb;--surface:#fff;--surface-2:#fbfcfe;--border:#e6e9f2;--border-strong:#d4d9e8;
  --text:#1e2433;--muted:#5b6478;--soft:#8a93a8;--primary:#4f46e5;--primary-soft:#eef0ff;
  --primary-border:#c7cdf5;--ok:#16a34a;--ok-bg:#e8f6ee;--no:#dc2626;--no-bg:#fbeaea;
  --shadow:0 1px 2px rgba(16,24,40,.04),0 8px 24px rgba(16,24,40,.06);--radius:14px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:15px;line-height:1.7;-webkit-font-smoothing:antialiased}
header.hero{background:linear-gradient(135deg,#4f46e5 0%,#6366f1 45%,#8b5cf6 100%);color:#fff;padding:38px 48px 34px}
header.hero h1{margin:0 0 6px;font-size:26px;font-weight:700}
header.hero .sub{opacity:.92;font-size:14px}
header.hero .meta{margin-top:14px;display:flex;gap:8px;flex-wrap:wrap}
header.hero .tag{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:3px 12px;font-size:12px}
.layout{display:flex;align-items:flex-start;max-width:1480px;margin:0 auto}
nav.toc{position:sticky;top:0;align-self:flex-start;width:236px;flex:0 0 236px;padding:26px 16px 26px 28px;font-size:13.5px;max-height:100vh;overflow-y:auto}
nav.toc .toc-title{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--soft);margin:0 0 10px;font-weight:600}
nav.toc ol{list-style:none;margin:0;padding:0}
nav.toc li{margin:2px 0}
nav.toc a{color:var(--muted);text-decoration:none;display:block;padding:6px 10px;border-radius:8px;border-left:2px solid transparent;transition:all .15s}
nav.toc a:hover{background:var(--surface);color:var(--primary)}
nav.toc a.active{background:var(--primary-soft);color:var(--primary);font-weight:600;border-left-color:var(--primary)}
main{flex:1;min-width:0;padding:28px 48px 80px}
section{margin-bottom:42px;scroll-margin-top:20px}
section h2{font-size:20px;font-weight:700;margin:0 0 4px;display:flex;align-items:center;gap:10px}
section h2 .num{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:8px;background:var(--primary);color:#fff;font-size:14px;font-weight:700}
section h3{font-size:16px;font-weight:600;margin:22px 0 8px}
.lead{color:var(--muted);font-size:14px;margin:0 0 16px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden;margin:14px 0}
.card .card-head{padding:12px 18px;border-bottom:1px solid var(--border);background:var(--surface-2);font-size:13px;font-weight:600;color:var(--muted);display:flex;align-items:center;gap:8px;justify-content:space-between}
.card .card-head .left{display:flex;align-items:center;gap:8px}
.card .card-head .dot{width:8px;height:8px;border-radius:50%;background:var(--primary)}
.card .card-head .zoom-btn{cursor:pointer;border:1px solid var(--border-strong);background:#fff;color:var(--muted);border-radius:8px;padding:4px 12px;font-size:12px;font-weight:600;transition:all .15s;user-select:none}
.card .card-head .zoom-btn:hover{background:var(--primary);color:#fff;border-color:var(--primary)}
.diagram-wrap{padding:22px 18px;overflow-x:auto;text-align:center;cursor:zoom-in;position:relative;min-height:60px}
.diagram-wrap .mermaid{display:inline-block}
.diagram-wrap svg{max-width:100%;height:auto;display:inline-block}
.notes{margin:14px 0}
.notes ul{margin:6px 0 0;padding-left:20px}
.notes li{margin:5px 0;color:var(--muted);font-size:14px}
.notes li strong{color:var(--text)}
.callout{background:var(--primary-soft);border:1px solid var(--primary-border);border-left:3px solid var(--primary);border-radius:10px;padding:12px 16px;margin:14px 0;font-size:14px}
.callout.warn{background:#fff7ed;border-color:#fed7aa;border-left-color:#f59e0b}
code{font-family:"JetBrains Mono","SF Mono",Menlo,Consolas,monospace;font-size:12.5px;background:#eef0f6;color:#3a3f55;padding:1px 6px;border-radius:5px}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin:10px 0 4px;font-size:12.5px;color:var(--muted)}
.legend .item{display:inline-flex;align-items:center;gap:6px}
.legend .swatch{width:12px;height:12px;border-radius:4px}
.table-wrap{overflow-x:auto;margin:14px 0;border-radius:var(--radius);border:1px solid var(--border);box-shadow:var(--shadow)}
table.matrix{width:100%;border-collapse:collapse;background:var(--surface);font-size:13.5px;min-width:720px}
table.matrix thead th{background:#1e2433;color:#fff;text-align:left;padding:12px 14px;font-weight:600;font-size:13px;position:sticky;top:0}
table.matrix thead th.center{text-align:center}
table.matrix td,table.matrix th{padding:9px 14px;border-bottom:1px solid var(--border)}
table.matrix tbody tr:nth-child(even){background:var(--surface-2)}
table.matrix tbody tr:hover{background:#f3f5fc}
table.matrix .res{font-weight:600;color:var(--text);white-space:nowrap}
table.matrix .grp td{background:#f0f2fa;font-weight:600;color:var(--primary)}
table.matrix .code{font-family:"JetBrains Mono",monospace;font-size:12.5px;color:var(--muted)}
.badge{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;font-weight:700;font-size:13px}
.badge.ok{background:var(--ok-bg);color:var(--ok)}
.badge.no{background:var(--no-bg);color:var(--no)}
.center{text-align:center}
footer{padding:28px 48px;text-align:center;color:var(--soft);font-size:12.5px;border-top:1px solid var(--border);background:var(--surface)}
/* lightbox */
#zoom-modal{position:fixed;inset:0;background:rgba(15,18,28,.94);display:none;z-index:1000;backdrop-filter:blur(4px)}
#zoom-modal.open{display:flex;flex-direction:column}
#zoom-modal .zm-bar{display:flex;align-items:center;justify-content:space-between;padding:14px 22px;color:#cfd5e6;font-size:13px;flex:0 0 auto}
#zoom-modal .zm-bar .hint{opacity:.8}
#zoom-modal .zm-bar .ctrls{display:flex;gap:8px;align-items:center}
#zoom-modal .zm-bar button{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.18);color:#fff;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px;font-weight:600}
#zoom-modal .zm-bar button:hover{background:var(--primary)}
#zoom-modal .zoom-stage{flex:1;overflow:hidden;position:relative;cursor:grab}
#zoom-modal .zoom-stage.dragging{cursor:grabbing}
#zoom-modal .zoom-inner{position:absolute;top:50%;left:50%;transform-origin:center center;transform:translate(-50%,-50%) scale(1)}
#zoom-modal .zoom-inner svg{width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;display:block;background:#fff;border-radius:8px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
@media (max-width:1080px){nav.toc{display:none}main{padding:24px 20px 60px}header.hero{padding:30px 24px}}
"""

# mermaid init + zoom logic
APP_JS = r"""
mermaid.initialize({
  startOnLoad:false,
  theme:'base',
  themeVariables:{
    fontFamily:'"Inter","PingFang SC","Microsoft YaHei",sans-serif',
    fontSize:'14px',
    primaryColor:'#eef2ff',primaryTextColor:'#1e2433',primaryBorderColor:'#6366f1',
    lineColor:'#94a3b8',secondaryColor:'#f1f5f9',tertiaryColor:'#fbfcfe',
    nodeBorder:'#c7cdf5',edgeLabelBackground:'#ffffff',
    clusterBkg:'#fbfcfe',clusterBorder:'#cbd5e1',
    mainBkg:'#eef2ff',actorBkg:'#eef2ff',actorBorder:'#6366f1',actorTextColor:'#1e2433',
    signalColor:'#475569',signalTextColor:'#1e2433',
    labelBoxBkgColor:'#eef2ff',labelBoxBorderColor:'#6366f1',labelTextColor:'#1e2433',
    noteBkgColor:'#fff7ed',noteBorderColor:'#fed7aa',noteTextColor:'#1e2433'
  },
  flowchart:{curve:'basis',htmlLabels:true,nodeSpacing:60,rankSpacing:70,padding:20,useMaxWidth:false},
  sequence:{actorMargin:60,mirrorActors:false,useMaxWidth:true},
  er:{useMaxWidth:true}
});

document.querySelectorAll('script[type="text/mermaid"]').forEach(function(s){
  var t=document.getElementById(s.dataset.target);
  if(t) t.textContent=s.textContent.trim();
});

mermaid.run({querySelector:'.mermaid'}).then(function(){
  document.querySelectorAll('.diagram-wrap').forEach(function(w){w.classList.add('ready');});
}).catch(function(e){console.error('mermaid run error:',e);});

(function(){
  var modal=document.getElementById('zoom-modal');
  var stage=modal.querySelector('.zoom-stage');
  var inner=modal.querySelector('.zoom-inner');
  var zlabel=document.getElementById('zlabel');
  var cur=null,home=null,scale=1,tx=0,ty=0,dragging=false,sx=0,sy=0,ox=0,oy=0;
  function apply(){inner.style.transform='translate(calc(-50% + '+tx+'px), calc(-50% + '+ty+'px)) scale('+scale+')';zlabel.textContent=Math.round(scale*100)+'%';}
  function open(wrap){
    var svg=wrap.querySelector('svg');if(!svg)return;
    cur=svg;home=wrap;
    // fit to viewport using viewBox aspect
    var vb=svg.viewBox&&svg.viewBox.baseVal;var ratio;
    if(vb&&vb.width>0&&vb.height>0){ratio=vb.width/vb.height;}else{ratio=1.6;}
    var maxW=window.innerWidth*0.94,maxH=window.innerHeight*0.82;
    var w=maxW,h=w/ratio;if(h>maxH){h=maxH;w=h*ratio;}
    inner.style.width=w+'px';inner.style.height=h+'px';
    scale=1;tx=0;ty=0;apply();
    inner.appendChild(svg);
    modal.classList.add('open');document.body.style.overflow='hidden';
  }
  function close(){if(cur&&home){home.appendChild(cur);}modal.classList.remove('open');document.body.style.overflow='';cur=null;home=null;}
  document.addEventListener('click',function(e){
    var btn=e.target.closest('.zoom-btn');
    if(btn){e.stopPropagation();var card=btn.closest('.card');var w=card.querySelector('.diagram-wrap');open(w);return;}
    var w=e.target.closest('.diagram-wrap');
    if(w&&w.querySelector('svg'))open(w);
  });
  modal.querySelector('.zm-close').addEventListener('click',close);
  modal.querySelector('.zm-reset').addEventListener('click',function(){scale=1;tx=0;ty=0;apply();});
  modal.querySelector('.zm-in').addEventListener('click',function(){scale=Math.min(8,scale*1.25);apply();});
  modal.querySelector('.zm-out').addEventListener('click',function(){scale=Math.max(.3,scale/1.25);apply();});
  modal.addEventListener('click',function(e){if(e.target===modal)close();});
  document.addEventListener('keydown',function(e){if(e.key==='Escape'&&modal.classList.contains('open'))close();});
  stage.addEventListener('wheel',function(e){
    e.preventDefault();
    var delta=e.deltaY<0?1.12:1/1.12;var ns=Math.max(.3,Math.min(8,scale*delta));
    var r=stage.getBoundingClientRect();var cx=e.clientX-r.left-r.width/2;var cy=e.clientY-r.top-r.height/2;
    tx=cx-(cx-tx)*(ns/scale);ty=cy-(cy-ty)*(ns/scale);scale=ns;apply();
  },{passive:false});
  stage.addEventListener('mousedown',function(e){dragging=true;sx=e.clientX;sy=e.clientY;ox=tx;oy=ty;stage.classList.add('dragging');});
  window.addEventListener('mousemove',function(e){if(!dragging)return;tx=ox+(e.clientX-sx);ty=oy+(e.clientY-sy);apply();});
  window.addEventListener('mouseup',function(){dragging=false;stage.classList.remove('dragging');});
  stage.addEventListener('dblclick',function(){scale=1;tx=0;ty=0;apply();});
  // TOC
  var sections=document.querySelectorAll('section[id]');
  var links=document.querySelectorAll('nav.toc a');
  var io=new IntersectionObserver(function(entries){entries.forEach(function(e){if(e.isIntersecting){links.forEach(function(l){l.classList.remove('active');});var a=document.querySelector('nav.toc a[href="#'+e.target.id+'"]');if(a)a.classList.add('active');}});},{rootMargin:'-20% 0px -70% 0px'});
  sections.forEach(function(s){io.observe(s);});
})();
"""

TABLE = """<table class="matrix">
<thead><tr><th>资源类型</th><th>动作 code</th><th class="center">平台管理员</th><th class="center">组管理员</th><th>说明</th></tr></thead>
<tbody>
<tr class="grp"><td colspan="5">litellm · 模型网关</td></tr>
<tr><td class="res">litellm</td><td class="code">model:manage</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge no">✗</span></td><td>全局上游模型/供应商，不受组范围限制</td></tr>
<tr><td class="res"></td><td class="code">key:manage</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>所属 UserGroup 对应 Team 的虚拟 key</td></tr>
<tr><td class="res"></td><td class="code">spend:view</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>所属 UserGroup 用量与成本</td></tr>
<tr class="grp"><td colspan="5">agent_definition · 智能体开发层</td></tr>
<tr><td class="res">agent_definition</td><td class="code">view / create / update</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>查看/创建/编辑草稿配置</td></tr>
<tr><td class="res"></td><td class="code">delete</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge no">✗</span></td><td>组管理员不可删定义</td></tr>
<tr><td class="res"></td><td class="code">publish / manage_skills</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>发布版本快照 / 技能管理</td></tr>
<tr class="grp"><td colspan="5">resource_pool · 运行资源管理层</td></tr>
<tr><td class="res">resource_pool</td><td class="code">view</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>只读</td></tr>
<tr><td class="res"></td><td class="code">create / update / delete / clone</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge no">✗</span></td><td>资源池仅平台管理员维护</td></tr>
<tr class="grp"><td colspan="5">agent_instance · 智能体实例层</td></tr>
<tr><td class="res">agent_instance</td><td class="code">view / create / update / delete / clone</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>实例 CRUD + 克隆</td></tr>
<tr><td class="res"></td><td class="code">publish / offline</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>上线 / 停用</td></tr>
<tr><td class="res"></td><td class="code">switch_version / manage_channel</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>切换版本回滚 / 管理 IM 渠道</td></tr>
<tr><td class="res"></td><td class="code">deploy / suspend / resume / restart / destroy</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>运行时全生命周期</td></tr>
<tr><td class="res"></td><td class="code">view_overview / metrics / memory / logs</td><td class="center"><span class="badge ok">✓</span></td><td class="center"><span class="badge ok">✓</span></td><td>概览/监控/记忆/日志</td></tr>
</tbody></table>"""

def diagram(name, caption):
    return ('<div class="card"><div class="card-head"><span class="left"><span class="dot"></span>%s</span>'
            '<span class="zoom-btn">🔍 放大查看</span></div>'
            '<div class="diagram-wrap"><div class="mermaid" id="%s"></div></div></div>'
            ) % (html.escape(caption), name)

def mermaid_src(name):
    return '<script type="text/mermaid" data-target="%s">%s</script>' % (name, mmd(name))

def h2(num, title, sid): return '<section id="%s"><h2><span class="num">%s</span>%s</h2>' % (sid, num, title)
def lead(t): return '<p class="lead">%s</p>' % t
def h3(t): return '<h3>%s</h3>' % t
def callout(t, kind=""): return '<div class="callout %s">%s</div>' % (kind, t)
def notes(items): return '<div class="notes"><ul>'+''.join('<li>%s</li>'%i for i in items)+'</ul></div>'

LEGEND = '<div class="legend"><span class="item"><span class="swatch" style="background:#6366f1"></span>接入</span><span class="item"><span class="swatch" style="background:#0ea5e9"></span>服务</span><span class="item"><span class="swatch" style="background:#f59e0b"></span>编排</span><span class="item"><span class="swatch" style="background:#10b981"></span>引擎</span><span class="item"><span class="swatch" style="background:#8b5cf6"></span>模型网关</span><span class="item"><span class="swatch" style="background:#64748b"></span>基础设施</span></div>'

body = []
body.append(callout('架构图均基于 V3 真实代码核对，由 mermaid 在浏览器内运行时渲染（使用你本机的中文字体，无文字遮挡），<strong>mermaid 库已内联，离线可用</strong>。<strong>点击任意图表或「放大查看」</strong>全屏查看，支持滚轮缩放 / 拖拽平移 / 双击复位。'))

body.append(h2('1','系统整体架构图','s1')); body.append(lead('六层视图：用户接入层 / 应用服务层 / 运行编排层 / 引擎运行层 / 模型网关层 / 数据与基础设施。')); body.append(LEGEND)
body.append(diagram('m1','系统整体架构'))
body.append(notes(['<strong>Gateway 反向依赖解耦</strong>：不查 Controller 取 upstream，靠 <code>X-Agent-ID</code> + DNS 命名规范直连引擎；Controller 只管 Pod 生命周期与采样。','<strong>统一模型网关</strong>：引擎全部经 LiteLLM 调上游，per-instance key 保证用量精确归因到计费 Team（Team = UserGroup）。']))

body.append(h2('2','逻辑架构（V3 三层 + 横切）','s2')); body.append(lead('智能体开发层 / 运行资源管理层 / 智能体实例层，叠加 RBAC、LiteLLM 计费、Metrics 横切关注点。'))
body.append(diagram('m2','逻辑架构'))
body.append(notes(['<strong>引擎类型不建表</strong>：<code>engine_type</code> 作枚举放 definition，镜像/端口走 <code>ENGINE_RUNTIMES</code> 常量。','<strong>发布语义拆分</strong>：定义「发布版本」(生成快照) ≠ 实例「上线」(DRAFT→PUBLISHED)。实例绑定 <code>version_id</code> 支持回滚。','<strong>技能挂定义层</strong>，fan-out 到各实例；access_scope 在实例层决定谁能用。','<strong>运行时操作全归实例层</strong>：deploy / suspend / resume / restart / destroy。']))

body.append(h2('3','顶层模型关系图','s3')); body.append(lead('ER 关系（1:N / N:N / 引用）+ 以企业微信为例的引用链。'))
body.append(h3('3a. ER 关系')); body.append(diagram('m3a','实体关系图'))
body.append(h3('3b. 以企业微信为例的引用链')); body.append(callout('题目要求链路：企业微信应用 → 智能体实例 → 智能体定义 → 运行资源 → 单用户 Profile'))
body.append(diagram('m3b','企业微信引用链'))
body.append(notes(['<strong>引用链语义</strong>：企业微信应用 → 实例（渠道 FK）→ 定义 + 版本（可回滚）→ 资源池 → 部署（引擎 Pod）→ 单用户 Profile（Pod 内隔离会话空间）。','<strong>access_scope</strong> 在实例层决定谁能用，渠道层只做存在性检查。']))

body.append(h2('4','运行时序图（最终用户视角入口）','s4')); body.append(lead('Web Portal 聊天主路径 + 企业微信 IM 入站，均汇入「Gateway → 引擎 Pod → LiteLLM → 上游」同一调用链。'))
body.append(h3('4a. Web Portal 聊天入口（主路径）')); body.append(diagram('m4a','Web Portal 时序'))
body.append(h3('4b. IM 渠道入口（企业微信 inbound）')); body.append(diagram('m4b','企业微信 IM 时序'))
body.append(notes(['<strong>Web 入口</strong>：Portal 主动建会话、Gateway 透传 SSE（nginx 必须 <code>proxy_buffering off</code>，去掉 <code>Origin</code>/<code>Referer</code> 头避免引擎 403）。','<strong>IM 入口</strong>：dispatcher 被动收回调，做去重 + 权限闸门 + ensure 引擎就绪后转发，响应按 ≤2048 字节分段回发。']))

body.append(h2('5','k3s 部署拓扑图','s5')); body.append(lead('单 namespace unionagents。Ingress 双域名分流；引擎 Pod 由 Controller 动态创建为 Deployment + Service + PVC。'))
body.append(diagram('m5','k3s 部署拓扑'))
body.append(notes(['<strong>单 namespace</strong>：靠 DNS 命名 <code>engine-hermes-{id[:8]}[-{scope}].unionagents.svc:8642</code> 路由。','<strong>引擎动态创建</strong>：Deployment + 同名 Service + PVC <code>engine-data-{id[:8]}</code>（挂 <code>/opt/data</code>）。','<strong>Ingress 双域名</strong>：<code>admin.__DOMAIN__</code> / <code>chat.__DOMAIN__</code>，<code>/api</code> 拆分到 manager/gateway/controller。','<strong>持久化</strong>：PG（10Gi，含 unionagents + litellm 两个库）、MinIO（20Gi）、引擎 PVC（RWO）。']))

body.append(h2('6','RBAC 权限矩阵图','s6')); body.append(lead('权限模型 Role ⟂ Permission（N:N）、User ⟂ Role（N:N），粒度 = resource_type + code。管理台走 RBAC；终端用户走 access_scope。'))
body.append(h3('6a. 权限模型与角色')); body.append(diagram('m6a','RBAC 实体关系'))
body.append(callout('终端用户访问<strong>不走 RBAC</strong>，由 <code>AgentInstance.access_scope</code>（ALL / USER / USER_GROUP）决定可见性（详见 6c）。'))
body.append(h3('6b. 预置角色 × 权限矩阵')); body.append('<div class="table-wrap">'+TABLE+'</div>')
body.append(callout('平台管理员 = 全部权限；组管理员 = 实例全生命周期 + 定义开发 + 资源池只读 + LiteLLM key/spend，不可删定义、不可改资源池、不可管全局模型。'))
body.append(h3('6c. 终端用户 access_scope 与计费 Team 派生')); body.append(lead('计费 Team 由 access_scope 派生（谁能用谁出钱）。')); body.append(diagram('m6c','access_scope 派生'))
body.append(notes(['<code>_derive_team</code>：<code>USER_GROUP</code> → 首个组对应 Team；<code>ALL</code>/<code>USER</code> → 平台默认 Team（<code>default</code>）。','access 变更触发 per-instance key 重生成。']))

body.append('<h2 style="margin-top:48px;font-size:22px">核心业务流程图</h2>'); body.append(callout('以下 6 张图聚焦 V3 的关键运行机制，便于理解请求在系统内的实际流转。','warn'))

body.append(h2('7','Profile 分配与挂载流程','s7')); body.append(lead('用户首次发起会话时，Gateway + Controller 如何派生 profile、复用/新建 Pod、挂载隔离会话空间。'))
body.append(diagram('m7','Profile 分配与挂载'))
body.append(notes(['<strong>profile_name</strong> = agent_id[:8] + scope_hash[:6] + user_id[:8]，scope_hash = sha256(scope_type:scope_target)[:6]。','<strong>Pod 复用决策</strong>：<code>_select_pod_by_load</code> 按 <code>max_sessions_per_pod</code>（默认 20）选未满 Pod；全满则 <code>_ensure_pod_exists</code> 新建（seq 递增）。','<strong>多 Profile 共享 Pod</strong>：<code>internal_port_map</code> = {profiles:{name:port}, next_port:8644}，每 Profile 独立端口 + 独立目录 <code>/opt/data/profiles/{name}</code>，nginx 按端口反代。','<strong>AgentProfile 字段</strong>：profile_name / hermes_home / internal_port / user_id / group_id / deployment_id / profile_type。']))

body.append(h2('8','网关动态路由流程','s8')); body.append(lead('Gateway 收到请求后，如何用 X-Agent-ID 定位实例、校验权限、解析 Profile、构造引擎 URL 并透传。'))
body.append(callout('<strong>反向依赖解耦</strong>：Gateway 不查 Controller 取 upstream，靠 <code>X-Agent-ID</code> + DNS 命名（<code>{pod}.{ns}.svc.cluster.local:8642</code>）直连引擎。'))
body.append(diagram('m8','网关动态路由'))
body.append(notes(['<code>_get_agent</code> 只查 <code>status=PUBLISHED</code> 的实例；<code>_check_access</code> 按 access_scope 校验；<code>_match_channel</code> 查 enabled 渠道。','<strong>proxy 透传前去掉 host/origin/referer/x-hermes-profile 头</strong>（Hermes 引擎见 Origin 头会返回 403）。']))

body.append(h2('9','引擎生命周期与存档','s9')); body.append(lead('Deployment 状态机 + idle 自动回收 + MinIO 存档策略。'))
body.append(diagram('m9','引擎生命周期状态机'))
body.append(notes(['<strong>状态枚举</strong>：PENDING / DEPLOYING / RUNNING / SUSPENDED / FAILED / ARCHIVED。','<strong>idle 回收</strong>：suspend_loop 每 5min 检查（idle_suspend_minutes=30）；cleanup_loop 每 1h 检查（idle_destroy_hours=24）。','<strong>存档策略</strong>：SUSPEND 即归档（scale0 + tar→MinIO <code>backups/</code>）；PVC 实时写零开销，resume 优先用 PVC；DESTROY 仅清 K8s，数据转 <code>archives/</code> 永久留存。','<strong>restart</strong> = rollout_restart（annotation 触发，不改副本数）；<strong>resume</strong> SUSPENDED→RUNNING，Deployment 缺失返回 409。']))

body.append(h2('10','LiteLLM 计费归因流程','s10')); body.append(lead('per-instance key 生成、计费 Team 派生、用量归因与监控取数。'))
body.append(diagram('m10','LiteLLM 计费归因'))
body.append(notes(['<strong>per-instance key</strong>：<code>_provision_litellm</code> 调 <code>/key/generate</code>，metadata={instance_id,group_id}，alias=instance:{id[:8]}，归属 <code>_derive_team</code> 派生的 Team。','<strong>计费 Team 派生</strong>：USER_GROUP→access_groups[0] Team；ALL/USER→默认 Team(default)。<strong>谁能用谁出钱</strong>。','<strong>access 变更</strong>触发 key 重生成；<code>metrics_service</code> 用 key 查 <code>/spend/logs</code>（start_date 过滤，end_date+1 规避 quirk）。']))

body.append(h2('11','版本发布与回滚流程','s11')); body.append(lead('定义发布版本快照 → 实例绑定 → 上线 → 切换版本升级/回滚。'))
body.append(diagram('m11','版本发布与回滚'))
body.append(notes(['<strong>定义「发布版本」</strong>(publish_definition)：草稿配置拷贝成不可变 AgentVersion 快照（version_no=1.0.n + configs + engine_type），更新 current_version_id。','<strong>实例「上线」</strong>(publish_instance)：DRAFT→PUBLISHED 对终端可见，ensure http channel。<strong>≠ 定义发布</strong>。','<strong>switch_version</strong>：更新 inst.version_id + regen litellm key，controller <code>_load_instance_config</code> 读新版本，restart 生效，支持回滚。']))

body.append(h2('12','技能安装 fan-out 流程','s12')); body.append(lead('技能挂定义层，安装/开关/卸载 fan-out 到定义下所有 PUBLISHED 实例。'))
body.append(diagram('m12','技能 fan-out'))
body.append(notes(['<strong>技能挂定义层</strong>（<code>agent_definitions.skill_config</code>）；<code>_definition_instance_ids</code> 找 PUBLISHED 实例 fan-out。','<strong>install/uninstall</strong>：解压到 <code>/opt/data/profiles/{pn}/skills/{name}/</code> + 软链接 + 重启 Pod；<strong>sync</strong>：重写 config.yaml <code>skills.disabled</code> 热生效不重启。','<code>_load_skill_config</code> 用 json round-trip 深拷贝，防 ORM 跳过 UPDATE 的 bug。']))

# collect mermaid sources (one block per diagram)
src_names = ['m1','m2','m3a','m3b','m4a','m4b','m5','m6a','m6c','m7','m8','m9','m10','m11','m12']
sources = '\n'.join(mermaid_src(n) for n in src_names)

toc_items = [('s1','系统整体架构'),('s2','逻辑架构（三层）'),('s3','顶层模型关系'),('s4','运行时序图'),('s5','k3s 部署拓扑'),('s6','RBAC 权限矩阵'),('s7','Profile 分配挂载'),('s8','网关动态路由'),('s9','引擎生命周期与存档'),('s10','LiteLLM 计费归因'),('s11','版本发布与回滚'),('s12','技能 fan-out')]
toc = '<nav class="toc"><div class="toc-title">目录</div><ol>'+''.join('<li><a href="#%s">%s</a></li>'%(i,t) for i,t in toc_items)+'</ol></nav>'

zoom_modal = """<div id="zoom-modal">
  <div class="zm-bar"><span class="hint">滚轮缩放 · 拖拽平移 · 双击复位 · Esc 关闭</span>
    <span class="ctrls"><span id="zlabel" style="min-width:48px;text-align:center">100%</span>
      <button class="zm-out">－</button><button class="zm-in">＋</button><button class="zm-reset">复位</button><button class="zm-close">✕ 关闭</button>
    </span></div>
  <div class="zoom-stage"><div class="zoom-inner"></div></div>
</div>"""

doc = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>UnionAgents 知行 · V3 架构图</title>
<style>%s</style>
</head>
<body>
<header class="hero">
  <h1>UnionAgents（知行）· V3 架构图</h1>
  <div class="sub">基于 2026-06-23 完成的 V3 三层重构（智能体开发 / 运行资源管理 / 智能体实例 分离）</div>
  <div class="meta"><span class="tag">三层模型</span><span class="tag">多 Profile 隔离</span><span class="tag">LiteLLM 统一模型网关</span><span class="tag">K8s 容器化</span><span class="tag">企业微信 / 飞书</span><span class="tag">运行时渲染 · 离线可用</span></div>
</header>
<div class="layout">%s
<main>%s</main>
</div>
%s
<footer>UnionAgents 知行 · V3 架构文档 &nbsp;|&nbsp; 2026-06-23 &nbsp;|&nbsp; mermaid 运行时渲染（库已内联，离线可用）· 点击图表可全屏缩放查看</footer>
%s
<script>%s</script>
<script>%s</script>
</body>
</html>
""" % (CSS, toc, ''.join(body), zoom_modal, sources, MERMAID_JS, APP_JS)

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(doc)
print('wrote', TARGET, len(doc), 'bytes')
