# -*- coding: utf-8 -*-
"""
控制台前端页面 —— 单文件内联的 HTML/CSS/JS（无外部 CDN，离线可用，方便 exe 打包）。

为什么内联：PyInstaller 打包 exe 时多一个模板目录就多一处 --add-data 的坑，
整个页面作为一个 Python 字符串常量最省事，也不会出现路径找不到的问题。

页面结构（单页应用，原生 JS，无框架）：
  登录页 -> 概览 / 聚宽信号 / 手动下单 / 账户持仓 / 任务队列 / 用户管理 / 审计日志

v1.5.0：整体美化 + 手机/PC 自适应 + 新增「聚宽信号」页（交互详录）。
自适应要点：
  - <=820px：侧边导航变顶部横向滚动条；表单字段占满整行；按钮加大触控区；
    表格容器横向滚动（带惯性），顶部统计条自动隐藏次要项。
"""

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#2563eb">
<title>RelayGo · 控制台</title>
<style>
:root{
  --bg:#f3f5f9; --panel:#fff; --panel2:#f8fafd; --border:#e4e8f0;
  --text:#1c2434; --muted:#7c8798; --primary:#2563eb; --primary-d:#1d4ed8;
  --primary-bg:#e8f0fe;
  --ok:#16a34a; --warn:#d97706; --err:#dc2626; --up:#dc2626; --down:#16a34a;
  --shadow:0 1px 2px rgba(23,36,68,.05),0 3px 14px rgba(23,36,68,.06);
  --radius:12px;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  -webkit-text-size-adjust:100%}
.hidden{display:none !important}
button{font-family:inherit;font-size:13.5px;cursor:pointer;border:0;border-radius:8px;
  padding:8px 16px;min-height:36px;transition:filter .12s}
button:active{filter:brightness(.94)}
.btn{background:var(--primary);color:#fff}
.btn:hover{background:var(--primary-d)}
.btn.ghost{background:#eef2f8;color:var(--text)}
.btn.ghost:hover{background:#e3e9f4}
.btn.danger{background:#fee2e2;color:var(--err)}
.btn.danger:hover{background:#fecaca}
.btn.danger-solid{background:var(--err);color:#fff}
.btn.danger-solid:hover{filter:brightness(.9)}
.btn:disabled{opacity:.55;cursor:not-allowed}
input,select{font-family:inherit;font-size:14px;padding:8px 11px;border:1px solid var(--border);
  border-radius:8px;background:#fff;color:var(--text);outline:none;width:100%}
input:focus,select:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.ver{font-size:11px;color:var(--muted);font-weight:400}
code{background:#eef2f8;padding:1px 5px;border-radius:4px;font-size:12px;word-break:break-all}

/* ---------- 登录 ---------- */
.mask{position:fixed;inset:0;background:linear-gradient(140deg,#eef3fb,#dbe5f5);
  display:flex;align-items:center;justify-content:center;z-index:100;padding:16px}
.login-card{width:360px;max-width:100%;background:#fff;border-radius:18px;padding:34px 30px;
  box-shadow:0 22px 60px rgba(30,50,90,.16)}
.login-card h1{margin:0 0 4px;font-size:21px}
.login-card .sub{margin:0 0 22px;color:var(--muted);font-size:12.5px}
.login-card label{display:block;margin:12px 0 5px;font-size:12.5px;color:var(--muted)}
.login-card .btn{width:100%;margin-top:20px;padding:11px;font-size:15px}
.err{color:var(--err);font-size:12.5px;min-height:18px;margin-top:10px}
.hint{margin-top:14px;font-size:12px;color:var(--muted);line-height:1.7}

/* ---------- 顶栏 ---------- */
header{min-height:54px;background:#fff;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:18px;padding:8px 20px;position:sticky;top:0;z-index:20;
  box-shadow:0 1px 0 rgba(23,36,68,.02)}
.brand{font-weight:600;font-size:15px;white-space:nowrap}
.lic{white-space:nowrap;font-size:12px;border-radius:20px;padding:3px 12px;
  background:#e8f7ef;color:#00875a;border:1px solid #bfe8d2}
.lic.warn{background:#fff7e8;color:#b26b00;border-color:#f5dfb8}
.lic b{font-weight:600}
.hstats{display:flex;gap:16px;font-size:12.5px;color:var(--muted);flex:1;overflow:hidden;flex-wrap:wrap}
.hstats b{color:var(--text);font-weight:600}
.me{display:flex;align-items:center;gap:10px;font-size:13px;white-space:nowrap;margin-left:auto}
.badge{background:#eef2f8;color:var(--muted);border-radius:20px;padding:2px 10px;font-size:12px}
.link{background:none;color:var(--primary);padding:4px 6px;min-height:0}

/* ---------- 布局 ---------- */
.layout{display:flex;min-height:calc(100vh - 54px)}
nav{width:190px;background:#fff;border-right:1px solid var(--border);
  padding:14px 10px;flex-shrink:0}
nav a{display:flex;align-items:center;gap:9px;padding:10px 13px;border-radius:9px;
  color:var(--text);text-decoration:none;font-size:13.5px;margin-bottom:3px;
  border-left:3px solid transparent}
nav a:hover{background:#f2f5fa}
nav a.on{background:var(--primary-bg);color:var(--primary);font-weight:600;
  border-left-color:var(--primary)}
nav .icon{width:20px;text-align:center;flex-shrink:0}
main{flex:1;padding:20px 24px 40px;max-width:1180px;min-width:0}
h2{margin:0 0 16px;font-size:17.5px}
h3{margin:22px 0 10px;font-size:14.5px;color:var(--text)}
.card{background:#fff;border:1px solid var(--border);border-radius:var(--radius);
  padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.stat{background:#fff;border:1px solid var(--border);border-radius:var(--radius);
  padding:14px 16px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.stat:before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--primary);opacity:.55}
.stat.s-ok:before{background:var(--ok)} .stat.s-err:before{background:var(--err)}
.stat.s-warn:before{background:var(--warn)} .stat.s-muted:before{background:#c3cad6}
.stat .k{font-size:12.5px;color:var(--muted)}
.stat .v{font-size:23px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.stat .v.ok{color:var(--ok)} .stat .v.err{color:var(--err)} .stat .v.warn{color:var(--warn)}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.field{display:flex;flex-direction:column;gap:5px;min-width:0}
.field label{font-size:12.5px;color:var(--muted)}
.field.w1{width:170px} .field.w2{width:120px} .field.w3{width:150px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #eef1f6;white-space:nowrap}
th{background:var(--panel2);color:var(--muted);font-weight:600;font-size:12.5px;position:sticky;top:0;z-index:2}
tbody tr:hover{background:#f8fafd}
tr.expandable{cursor:pointer}
tr.detail-row td{background:#fbfcfe;white-space:normal}
.scroll{max-height:480px;overflow:auto;border:1px solid var(--border);
  border-radius:10px;-webkit-overflow-scrolling:touch}
.empty{color:var(--muted);font-size:13px;padding:18px;text-align:center}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.kv .item{background:var(--panel2);border:1px solid var(--border);border-radius:9px;padding:10px 13px}
.kv .item .k{font-size:12px;color:var(--muted)}
.kv .item .v{font-size:16px;font-weight:600;margin-top:3px;font-variant-numeric:tabular-nums;word-break:break-all}
.tag{display:inline-block;border-radius:20px;padding:2px 9px;font-size:12px;font-weight:500;white-space:nowrap}
.tag.pending,.tag.dispatched{background:#fff7e6;color:var(--warn)}
.tag.accepted{background:#e8f0fe;color:var(--primary)}
.tag.filled,.tag.done,.tag.ok{background:#e7f7ec;color:var(--ok)}
.tag.failed,.tag.expired,.tag.timeout,.tag.bad{background:#fdecec;color:var(--err)}
.tag.running{background:#e8f0fe;color:var(--primary)}
.buy{color:var(--up);font-weight:600} .sell{color:var(--down);font-weight:600}
.toast{position:fixed;right:22px;bottom:22px;background:#1f2937;color:#fff;padding:11px 18px;
  border-radius:9px;font-size:13px;opacity:0;transition:.25s;pointer-events:none;z-index:200;
  max-width:min(420px,86vw)}
.toast.on{opacity:1}
.toast.err{background:var(--err)} .toast.ok{background:var(--ok)}
.loading{color:var(--muted);font-size:13px;padding:12px 0}
.spin{display:inline-block;width:12px;height:12px;border:2px solid #cbd5e1;border-top-color:var(--primary);
  border-radius:50%;animation:sp .7s linear infinite;vertical-align:-1px;margin-right:6px}
@keyframes sp{to{transform:rotate(360deg)}}
.tabs{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.tabs button{background:#eef2f8;color:var(--text)}
.tabs button.on{background:var(--primary);color:#fff}
.note{font-size:12.5px;color:var(--muted);margin-top:10px;line-height:1.75}
.flexbetween{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.pager{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap}
.pager .info{font-size:12.5px;color:var(--muted)}

/* ---------- 信号时间线 ---------- */
.tl{margin:4px 0 0;padding:0}
.tl .step{position:relative;padding:0 0 14px 24px}
.tl .step:before{content:'';position:absolute;left:5px;top:4px;width:10px;height:10px;
  border-radius:50%;background:var(--primary)}
.tl .step:after{content:'';position:absolute;left:9.5px;top:17px;bottom:-1px;width:2px;
  background:#e4e8f0;border-radius:2px}
.tl .step:last-child{padding-bottom:2px}
.tl .step:last-child:after{display:none}
.tl .step.oks:before{background:var(--ok)} .tl .step.errs:before{background:var(--err)}
.tl .step.warns:before{background:var(--warn)}
.tl .t1{font-size:13px;font-weight:500}
.tl .t2{font-size:12px;color:var(--muted);word-break:break-all}

/* ---------- 手机 / 窄屏自适应 ---------- */
@media (max-width:820px){
  header{flex-wrap:wrap;padding:8px 12px;gap:8px}
  .brand{font-size:14px}
  .hstats{order:3;width:100%;gap:12px;overflow-x:auto;flex-wrap:nowrap;white-space:nowrap;
    padding-bottom:2px;-webkit-overflow-scrolling:touch}
  .me{gap:6px;font-size:12.5px}
  .layout{flex-direction:column}
  nav{width:100%;display:flex;overflow-x:auto;padding:8px 8px;border-right:0;
    border-bottom:1px solid var(--border);-webkit-overflow-scrolling:touch;
    position:sticky;top:54px;z-index:19;background:#fff}
  nav a{white-space:nowrap;margin:0 4px 0 0;border-left:0;border-bottom:3px solid transparent;
    border-radius:9px 9px 0 0;padding:8px 12px}
  nav a.on{border-bottom-color:var(--primary)}
  main{padding:14px 12px 60px}
  h2{font-size:16.5px}
  .card{padding:14px 14px;border-radius:10px}
  .row .field,.field.w1,.field.w2,.field.w3{width:100%;flex:1 1 100%}
  .row{gap:10px}
  button{min-height:42px;font-size:14.5px}   /* 触控友好 */
  .btn.ghost,.tabs button{min-height:38px}
  th,td{padding:9px 8px;font-size:12.5px;white-space:nowrap}
  .toast{left:12px;right:12px;bottom:14px;max-width:none;text-align:center}
  .login-card{padding:26px 20px}
  .kv{grid-template-columns:1fr 1fr}
}
@media (max-width:420px){ .kv{grid-template-columns:1fr} }
</style>
</head>
<body>

<div id="loginMask" class="mask">
  <div class="login-card">
    <h1>RelayGo</h1>
    <p class="sub">聚宽信号中转下单 · Web 控制台 <span class="ver">__VERSION__</span></p>
    <label>用户名</label>
    <input id="lgUser" autocomplete="username" placeholder="请输入用户名">
    <label>密码</label>
    <input id="lgPass" type="password" autocomplete="current-password" placeholder="请输入密码">
    <button class="btn" id="lgBtn">登 录</button>
    <div class="err" id="lgErr"></div>
    <div class="hint" id="lgHint"></div>
  </div>
</div>

<div id="app" class="hidden">
  <header>
    <div class="brand">RelayGo · 控制台 <span class="ver">__VERSION__</span></div>
    <div class="lic" id="licInfo" style="display:none"></div>
    <div class="hstats" id="hstats"></div>
    <div class="me">
      <span id="meName"></span><span class="badge" id="meRole"></span>
      <button class="link" id="pwBtn">改密码</button>
      <button class="link" id="outBtn">退出</button>
    </div>
  </header>
  <div class="layout">
    <nav id="nav"></nav>
    <main id="main"></main>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
/* ==================== 基础 ==================== */
var ME = null;
var TIMER = null;

function $(id){ return document.getElementById(id); }
function esc(s){
  return String(s===null||s===undefined?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function toast(msg, kind){
  var t = $('toast');
  t.textContent = msg;
  t.className = 'toast on' + (kind ? ' ' + kind : '');
  clearTimeout(t._t);
  t._t = setTimeout(function(){ t.className = 'toast'; }, 4200);
}
async function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign({'Content-Type':'application/json'}, opts.headers || {});
  opts.credentials = 'same-origin';
  var r = await fetch('/console/api' + path, opts);
  var data;
  try { data = await r.json(); } catch(e){ data = {ok:false, error:'服务端返回了非 JSON 内容'}; }
  if (r.status === 401){ showLogin(); throw new Error(data.error || '未登录'); }
  return data;
}
function post(path, body){ return api(path, {method:'POST', body: JSON.stringify(body||{})}); }
function kindTag(s){
  var map = {'pending':'待处理','dispatched':'已派发','accepted':'已受理','filled':'已成交',
             'failed':'失败','expired':'已过期','done':'成功','running':'执行中','timeout':'超时'};
  var cls = {'failed':'bad','expired':'bad','timeout':'bad','done':'ok','filled':'ok'};
  return '<span class="tag ' + (cls[s] || s) + '">' + esc(map[s] || s) + '</span>';
}
function sideText(s){
  return s === 'buy' ? '<span class="buy">买入</span>'
       : s === 'sell' ? '<span class="sell">卖出</span>' : esc(s);
}
function num(v){
  var n = parseFloat(v);
  if (isNaN(n)) return esc(v === null || v === undefined ? '-' : v);
  return n.toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
}
/* 动态表格：自动使用数据里的键作为列 */
function dynTable(rows){
  if (!rows || !rows.length) return '<div class="empty">没有数据</div>';
  var cols = Object.keys(rows[0]);
  var h = '<div class="scroll"><table><thead><tr>';
  cols.forEach(function(c){ h += '<th>' + esc(c) + '</th>'; });
  h += '</tr></thead><tbody>';
  rows.forEach(function(r){
    h += '<tr>';
    cols.forEach(function(c){ h += '<td>' + esc(r[c]) + '</td>'; });
    h += '</tr>';
  });
  return h + '</tbody></table></div>';
}

/* ==================== 登录 ==================== */
function showLogin(){
  if (TIMER){ clearInterval(TIMER); TIMER = null; }
  ME = null;
  $('loginMask').classList.remove('hidden');
  $('app').classList.add('hidden');
}
async function doLogin(){
  var u = $('lgUser').value.trim(), p = $('lgPass').value;
  $('lgErr').textContent = '';
  if (!u || !p){ $('lgErr').textContent = '请输入用户名和密码'; return; }
  $('lgBtn').disabled = true;
  try {
    var d = await post('/login', {username:u, password:p});
    if (!d.ok){ $('lgErr').textContent = d.error || '登录失败'; return; }
    $('lgPass').value = '';
    await boot();
  } catch(e){ $('lgErr').textContent = e.message; }
  finally { $('lgBtn').disabled = false; }
}
async function boot(){
  var d = await api('/me');
  if (!d.ok){ showLogin(); return; }
  ME = d.user;
  $('loginMask').classList.add('hidden');
  $('app').classList.remove('hidden');
  $('meName').textContent = ME.display_name || ME.username;
  $('meRole').textContent = ME.role_label;
  buildNav();
  location.hash = location.hash || '#overview';
  route();
  if (TIMER) clearInterval(TIMER);
  TIMER = setInterval(refreshHeader, 10000);
  refreshHeader();
}
async function refreshHeader(){
  try {
    var d = await api('/overview');
    if (!d.ok) return;
    var hb = d.last_heartbeat;
    var alive = hb && hb.seconds_ago !== null && hb.seconds_ago < 30;
    $('hstats').innerHTML =
      '执行端：<b style="color:' + (alive ? 'var(--ok)' : 'var(--err)') + '">'
      + (alive ? esc(hb.detail) + ' 在线' : (hb ? '离线（最后心跳 ' + esc(hb.ts) + '）' : '从未连接'))
      + '</b>' +
      '<span>待处理 <b>' + d.stats.pending + '</b></span>' +
      '<span>已受理 <b>' + d.stats.accepted + '</b></span>' +
      '<span>已成交 <b>' + d.stats.filled + '</b></span>' +
      '<span>失败 <b>' + d.stats.failed + '</b></span>';
  } catch(e){}
  refreshLicense();
}

/* ---------- 顶栏授权徽章 ---------- */
function refreshLicense(){
  var el = $('licInfo');
  // /license/api/state 不需要登录且授权拦截白名单内，任何状态都能读
  fetch('/license/api/state', {credentials:'same-origin'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      var st = d.state;
      if (st === 'disabled'){ el.style.display = 'none'; return; }   // 未启用授权：不显示
      if (st === 'ok' || st === 'grace'){
        var days = d.remaining_days;
        if ((days === null || days === undefined) && d.expire_date){
          var ms = new Date(String(d.expire_date).replace(' ', 'T')).getTime() - Date.now();
          days = ms > 0 ? Math.ceil(ms / 86400000) : 0;
        }
        var txt;
        if (d.is_permanent){
          txt = '授权：永久有效';
        } else {
          txt = '授权：剩余 <b>' + (days === null || days === undefined ? '-' : days) + ' 天</b> · '
              + esc(d.expire_date || '-') + ' 到期';
        }
        if (st === 'grace') txt += '（离线宽限中）';
        el.innerHTML = txt;
        el.className = 'lic' + (st === 'grace' ? ' warn' : '');
      } else {
        // blocked：理论上会被守门跳到激活页，这里兜底提示
        el.innerHTML = '授权：未激活';
        el.className = 'lic warn';
      }
      el.style.display = '';
    })
    .catch(function(){ el.style.display = 'none'; });
}

/* ==================== 导航 ==================== */
var NAV = [
  {id:'overview',  label:'概览',      icon:'▦', min:1},
  {id:'jqsignals', label:'聚宽信号',  icon:'⇅', min:1},
  {id:'order',     label:'手动下单',  icon:'⇄', min:2},
  {id:'account',   label:'账户查询',  icon:'◈', min:1},
  {id:'tasks',     label:'任务队列',  icon:'☰', min:1},
  {id:'users',     label:'用户管理',  icon:'⚇', min:3},
  {id:'audit',     label:'审计日志',  icon:'✎', min:3}
];
function buildNav(){
  var lv = ME.level;
  $('nav').innerHTML = NAV.filter(function(n){ return lv >= n.min; })
    .map(function(n){
      return '<a href="#' + n.id + '" data-id="' + n.id + '">'
           + '<span class="icon">' + n.icon + '</span>' + n.label + '</a>';
    }).join('');
}
function route(){
  var id = (location.hash || '#overview').slice(1);
  var item = NAV.filter(function(n){ return n.id === id; })[0];
  if (!item || ME.level < item.min) id = 'overview';
  Array.prototype.forEach.call($('nav').children, function(a){
    a.className = (a.dataset.id === id) ? 'on' : '';
  });
  ({overview:pageOverview, jqsignals:pageJqSignals, order:pageOrder, account:pageAccount,
    tasks:pageTasks, users:pageUsers, audit:pageAudit}[id] || pageOverview)();
}

/* ==================== 概览 ==================== */
async function pageOverview(){
  var m = $('main');
  m.innerHTML = '<h2>概览</h2><div class="loading"><span class="spin"></span>加载中…</div>';
  var d = await api('/overview');
  if (!d.ok){ m.innerHTML = '<h2>概览</h2><div class="card">' + esc(d.error) + '</div>'; return; }
  var s = d.stats, hb = d.last_heartbeat, t = d.tasks;
  var alarm = hb && hb.seconds_ago !== null && hb.seconds_ago < 30;
  m.innerHTML = '<h2>概览</h2>' +
    '<div class="cards">' +
      '<div class="stat"><div class="k">执行端</div><div class="v ' + (alarm ? 'ok' : 'err') + '">'
        + (alarm ? '在线' : '离线') + '</div></div>' +
      '<div class="stat s-warn"><div class="k">待处理信号</div><div class="v">' + s.pending + '</div></div>' +
      '<div class="stat"><div class="k">已受理</div><div class="v">' + s.accepted + '</div></div>' +
      '<div class="stat s-ok"><div class="k">已成交</div><div class="v">' + s.filled + '</div></div>' +
      '<div class="stat s-err"><div class="k">失败 / 过期</div><div class="v '
        + ((s.failed + s.expired) ? 'err' : '') + '">' + (s.failed + s.expired) + '</div></div>' +
      '<div class="stat s-muted"><div class="k">待办任务</div><div class="v">' + t.pending + '</div></div>' +
    '</div>' +
    '<div class="card"><div class="flexbetween"><h3 style="margin:0">运行信息</h3></div>' +
      '<div class="kv" style="margin-top:12px">' +
        '<div class="item"><div class="k">中转服务版本</div><div class="v">' + esc(d.version) + '</div></div>' +
        '<div class="item"><div class="k">最后心跳</div><div class="v" style="font-size:14px">'
          + esc(hb ? hb.ts : '从未') + '</div></div>' +
        '<div class="item"><div class="k">执行端标识</div><div class="v" style="font-size:14px">'
          + esc(hb ? hb.detail : '-') + '</div></div>' +
        '<div class="item"><div class="k">任务执行（成功/失败）</div><div class="v" style="font-size:14px">'
          + t.done + ' / ' + t.failed + '</div></div>' +
      '</div></div>' +
    '<h3>最近信号</h3><div class="note" style="margin:0 0 10px">完整的信号交互明细请看' +
      '<a href="#jqsignals" style="color:var(--primary)">「聚宽信号」</a>页。</div>' + signalTable(d.signals);
}
function signalTable(rows){
  if (!rows || !rows.length) return '<div class="card"><div class="empty">暂无信号</div></div>';
  var h = '<div class="scroll"><table><thead><tr>' +
    '<th>#</th><th>时间</th><th>代码</th><th>方向</th><th>数量</th><th>价格</th><th>状态</th><th>订单ID</th>' +
    '</tr></thead><tbody>';
  rows.forEach(function(x){
    h += '<tr><td>' + x.id + '</td><td>' + esc(x.created_at) + '</td><td>' + esc(x.security) +
      '</td><td>' + sideText(x.side) + '</td><td>' + x.amount + '</td><td>'
      + (x.price === null || x.price === undefined ? '市价' : num(x.price)) +
      '</td><td>' + kindTag(x.status) + '</td><td><code>' + esc(x.jq_order_id) + '</code></td></tr>';
  });
  return h + '</tbody></table></div>';
}

/* ==================== 聚宽信号（v1.5.0 交互详录） ==================== */
var JQ = {status:'', security:'', date:'', offset:0, limit:20, expanded:{}};
var JQ_STATUS = [
  ['all','全部状态'],['pending','待处理'],['dispatched','已派发'],['accepted','已受理'],
  ['filled','已成交'],['failed','失败'],['expired','已过期']
];
function pageJqSignals(){
  var m = $('main');
  m.innerHTML = '<h2>聚宽信号</h2>' +
    '<div class="note" style="margin:0 0 14px">与聚宽策略的完整交互记录：每条信号的' +
    '接收、派发、受理、成交/失败全过程，以及被中转拒绝的请求（密钥错误、字段缺失、积压拒收等）。</div>' +
    '<div class="card"><div class="row">' +
      '<div class="field w2"><label>状态</label><select id="jqStatus">' +
        JQ_STATUS.map(function(s){
          return '<option value="' + s[0] + '">' + s[1] + '</option>'; }).join('') +
      '</select></div>' +
      '<div class="field w2"><label>证券代码</label><input id="jqSec" placeholder="如 600519"></div>' +
      '<div class="field w2"><label>日期</label><input id="jqDate" type="date"></div>' +
      '<button class="btn" id="jqGo">查询</button>' +
      '<button class="btn ghost" id="jqReset">重置</button>' +
      '<button class="btn ghost" id="jqRefresh">刷新</button>' +
      (ME && ME.level >= 3 ?
        '<button class="btn danger" id="jqClearQ">清除待派发队列</button>' +
        '<button class="btn danger-solid" id="jqClearAll">一键清除全部记录</button>' : '') +
    '</div></div>' +
    '<div id="jqBody"><div class="loading"><span class="spin"></span>加载中…</div></div>' +
    '<h3>被拒绝的请求</h3>' +
    '<div id="jqRejects"><div class="loading"><span class="spin"></span>加载中…</div></div>';
  $('jqStatus').value = JQ.status;
  $('jqSec').value = JQ.security;
  $('jqDate').value = JQ.date;
  $('jqGo').onclick = function(){
    JQ.status = $('jqStatus').value; JQ.security = $('jqSec').value.trim();
    JQ.date = $('jqDate').value; JQ.offset = 0; JQ.expanded = {};
    loadJqSignals(); loadJqRejects();
  };
  $('jqReset').onclick = function(){
    JQ = {status:'', security:'', date:'', offset:0, limit:20, expanded:{}};
    pageJqSignals();
  };
  $('jqRefresh').onclick = function(){ loadJqSignals(); loadJqRejects(); };
  var jqClearQ = $('jqClearQ');
  if (jqClearQ) jqClearQ.onclick = async function(){
    if (!confirm('确认清除所有「待派发」的聚宽信号？\n这些信号尚未派发给执行端，清除后不会再下单（聚宽重发同订单号会重新受理）。\n\n此操作不可恢复。')) return;
    try {
      var d = await post('/jq_signals/clear', {scope:'pending'});
      toast(d.ok ? ('已清除待派发队列（' + d.deleted + ' 条）') : (d.error || '清除失败'),
           d.ok ? 'ok' : 'err');
      if (d.ok) loadJqSignals();
    } catch(e){ toast(e.message, 'err'); }
  };
  var jqClearAll = $('jqClearAll');
  if (jqClearAll) jqClearAll.onclick = function(){
    clearAllRecords(function(){
      JQ.offset = 0; JQ.expanded = {};
      loadJqSignals(); loadJqRejects();
    });
  };
  loadJqSignals();
  loadJqRejects();
}
async function loadJqSignals(){
  var box = $('jqBody');
  if (!box) return;
  box.innerHTML = '<div class="loading"><span class="spin"></span>加载中…</div>';
  var q = '?limit=' + JQ.limit + '&offset=' + JQ.offset +
    '&status=' + encodeURIComponent(JQ.status || 'all') +
    '&security=' + encodeURIComponent(JQ.security) +
    '&date=' + encodeURIComponent(JQ.date);
  var d;
  try { d = await api('/jq_signals' + q); } catch(e){ box.innerHTML = '<div class="card">' + esc(e.message) + '</div>'; return; }
  if (!d.ok){ box.innerHTML = '<div class="card">' + esc(d.error) + '</div>'; return; }
  var total = d.total, rj = (d.rejects && d.rejects.total) || 0;
  var h = '<div class="cards">' +
    '<div class="stat"><div class="k">符合条件</div><div class="v">' + total + '</div></div>' +
    '<div class="stat s-err"><div class="k">累计被拒请求</div><div class="v">' + rj + '</div></div>' +
    '</div>';
  if (!d.signals.length){
    h += '<div class="card"><div class="empty">没有符合条件的信号</div></div>';
  } else {
    h += '<div class="card" style="padding:0"><div class="scroll"><table><thead><tr>' +
      '<th style="width:26px"></th><th>#</th><th>时间</th><th>代码</th><th>方向</th><th>数量</th>' +
      '<th>价格</th><th>状态</th><th>来源IP</th><th>订单ID</th></tr></thead><tbody>';
    d.signals.forEach(function(x){
      h += '<tr class="expandable" data-id="' + x.id + '">' +
        '<td>' + (JQ.expanded[x.id] ? '▾' : '▸') + '</td>' +
        '<td>' + x.id + '</td><td>' + esc(x.created_at) + '</td><td>' + esc(x.security) +
        '</td><td>' + sideText(x.side) + '</td><td>' + x.amount + '</td><td>' +
        (x.price === null || x.price === undefined ? '市价' : num(x.price)) +
        '</td><td>' + kindTag(x.status) + '</td><td>' + esc(x.src_ip || '-') +
        '</td><td><code>' + esc(x.jq_order_id) + '</code></td></tr>';
      if (JQ.expanded[x.id]) h += detailRow(x);
    });
    h += '</tbody></table></div></div>';
    var page = Math.floor(JQ.offset / JQ.limit) + 1;
    var pages = Math.max(1, Math.ceil(total / JQ.limit));
    h += '<div class="pager">' +
      '<button class="btn ghost" id="jqPrev"' + (JQ.offset <= 0 ? ' disabled' : '') + '>上一页</button>' +
      '<span class="info">第 ' + page + ' / ' + pages + ' 页 · 共 ' + total + ' 条</span>' +
      '<button class="btn ghost" id="jqNext"' + (JQ.offset + JQ.limit >= total ? ' disabled' : '') + '>下一页</button></div>';
  }
  box.innerHTML = h;
  box.querySelectorAll('tr.expandable').forEach(function(tr){
    tr.onclick = function(){
      var id = parseInt(tr.dataset.id, 10);
      if (JQ.expanded[id]) delete JQ.expanded[id]; else JQ.expanded[id] = true;
      loadJqSignals();
    };
  });
  var prev = $('jqPrev'), next = $('jqNext');
  if (prev) prev.onclick = function(){ JQ.offset = Math.max(0, JQ.offset - JQ.limit); loadJqSignals(); };
  if (next) next.onclick = function(){ JQ.offset += JQ.limit; loadJqSignals(); };
}
function detailRow(x){
  var steps = [];
  steps.push({t:'收到聚宽信号', s:x.created_at, d:'订单ID ' + x.jq_order_id +
    (x.jq_status ? '（聚宽状态 ' + esc(x.jq_status) + '）' : '') +
    ' · 来源 ' + esc(x.src_ip || '-'), c:'oks'});
  if (x.dispatched_at) steps.push({t:'已派发给执行端', s:x.dispatched_at, d:'', c:''});
  (x.feedback || []).forEach(function(f){
    var cls = f.result === 'failed' ? 'errs' : 'oks';
    steps.push({t:kindTag(f.result) + ' 执行端回报', s:f.created_at, d:f.message || '', c:cls});
  });
  if (!x.dispatched_at && (x.status === 'expired'))
    steps.push({t:'信号已过期（超时未被领走）', s:'', d:'', c:'warns'});
  var h = '<tr class="detail-row"><td></td><td colspan="9"><div class="tl">';
  steps.forEach(function(st){
    h += '<div class="step ' + st.c + '"><div class="t1">' + st.t +
      (st.s ? ' <span class="t2" style="font-weight:400">· ' + esc(st.s) + '</span>' : '') + '</div>' +
      (st.d ? '<div class="t2">' + st.d + '</div>' : '') + '</div>';
  });
  return h + '</div></td></tr>';
}
async function loadJqRejects(){
  var box = $('jqRejects');
  if (!box) return;
  var d;
  try { d = await api('/jq_rejects?limit=30'); } catch(e){ box.innerHTML = '<div class="card">' + esc(e.message) + '</div>'; return; }
  if (!d.ok){ box.innerHTML = '<div class="card">' + esc(d.error) + '</div>'; return; }
  if (!d.rejects.length){
    box.innerHTML = '<div class="card"><div class="empty">没有被拒绝的请求记录 ✓</div></div>'; return;
  }
  var REASON = {'401_auth':'密钥无效','400_json':'报文格式错','400_field':'字段不合法','503_backlog':'积压拒收'};
  var h = '<div class="card" style="padding:0"><div class="scroll"><table><thead><tr>' +
    '<th>时间</th><th>接口</th><th>原因</th><th>详情</th><th>来源IP</th></tr></thead><tbody>';
  d.rejects.forEach(function(r){
    h += '<tr><td>' + esc(r.ts) + '</td><td><code>' + esc(r.endpoint) + '</code></td><td>' +
      '<span class="tag bad">' + esc(REASON[r.reason] || r.reason) + '</span></td><td>' +
      esc(r.detail) + '</td><td>' + esc(r.ip || '-') + '</td></tr>';
  });
  box.innerHTML = h + '</tbody></table></div></div>';
}

/* ==================== 证券代码后缀自动补全 ====================
   规则与后端 relay_server/security_code.py 保持一致：
   能判出市场就补后缀；判不出（北交所 / 指数 / 未知段）保持原样，交给后端报错。
   值 '' 表示"该前缀不可下单"，命中即不再往下猜（与后端 _BLOCKED 对应）。 */
var JQ_T3 = {688:'XSHG',689:'XSHG',900:'XSHG',110:'XSHG',111:'XSHG',113:'XSHG',118:'XSHG',
  204:'XSHG',300:'XSHE',301:'XSHE',159:'XSHE',123:'XSHE',127:'XSHE',128:'XSHE',131:'XSHE',
  200:'XSHE',399:'',430:'',920:''};
var JQ_T2 = {60:'XSHG',68:'XSHG',90:'XSHG',50:'XSHG',51:'XSHG',52:'XSHG',56:'XSHG',58:'XSHG',
  11:'XSHG',30:'XSHE',15:'XSHE',16:'XSHE',18:'XSHE',12:'XSHE',13:'XSHE',20:'XSHE',
  43:'',83:'',87:''};
var JQ_T1 = {6:'XSHG',5:'XSHG',7:'XSHG',0:'XSHE',3:'XSHE',1:'XSHE',2:'XSHE',4:'',8:'',9:'XSHG'};

function jqSuffix(code){
  var tables = [[3, JQ_T3], [2, JQ_T2], [1, JQ_T1]];
  for (var i = 0; i < tables.length; i++){
    var k = code.slice(0, tables[i][0]);
    if (Object.prototype.hasOwnProperty.call(tables[i][1], k)) return tables[i][1][k];
  }
  return '';
}

function jqNorm(v){
  var s = (v || '').replace(/[\s\u3000]/g, '').replace(/[\uFF01-\uFF5E]/g, function(ch){
    return String.fromCharCode(ch.charCodeAt(0) - 65248);
  }).toUpperCase();
  var m = s.match(/^(\d{6})(?:\.([A-Z]{2,4}))?$/);
  if (!m) return s;                       // 形态不对：不动，交给后端给提示
  var code = m[1], suf = m[2];
  if (suf){
    var alias = {XSHG:'XSHG', SH:'XSHG', SS:'XSHG', XSHE:'XSHE', SZ:'XSHE'}[suf];
    return alias ? code + '.' + alias : s;
  }
  var auto = jqSuffix(code);
  return auto ? code + '.' + auto : s;
}

/* ==================== 手动下单 ==================== */
function pageOrder(){
  $('main').innerHTML = '<h2>手动下单</h2>' +
    '<div class="card">' +
      '<div class="row">' +
        '<div class="field w1"><label>证券代码</label>' +
          '<input id="odCode" placeholder="510300.XSHG"></div>' +
        '<div class="field w2"><label>方向</label>' +
          '<select id="odSide"><option value="buy">买入</option><option value="sell">卖出</option></select></div>' +
        '<div class="field w2"><label>数量（股）</label>' +
          '<input id="odAmount" value="100"></div>' +
        '<div class="field w2"><label>价格（可空）</label>' +
          '<input id="odPrice" placeholder="市价"></div>' +
        '<button class="btn" id="odBtn">提交下单</button>' +
      '</div>' +
      '<div class="note">证券代码可<b>只填 6 位数字</b>（如 <code>510300</code>），系统按代码段自动补' +
        '<code>.XSHG</code>（沪）/<code>.XSHE</code>（深）；也可直接填 <code>510300.XSHG</code>。' +
        '后缀与代码矛盾（如 <code>510300.XSHE</code>）、北交所与指数代码会被拒绝。<br>' +
        '价格留空表示由执行端决定；填写时为限价委托，需在当日涨跌停区间内。<br>' +
        '下单会先经执行端风控（白名单 / 单笔上限 / 日限额 / 持仓核对 / 价格偏差），通过后才提交到券商客户端。</div>' +
    '</div>' +
    '<div id="odResult"></div>' +
    '<h3>撤销委托</h3>' +
    '<div class="card"><div class="row">' +
      '<div class="field w1"><label>委托号</label>' +
        '<input id="ccNo" placeholder="见下方当日委托的合同编号"></div>' +
      '<button class="btn danger" id="ccBtn">撤单</button>' +
      '<button class="btn danger-solid" id="ccaBtn">一键全撤</button>' +
      '<button class="btn ghost" id="clrBtn">清场</button>' +
      '<button class="btn warn" id="rsBtn">重启同花顺</button>' +
    '</div><div class="note">撤单走安全路径：读取同花顺撤单页，只有当可撤委托唯一且与委托号匹配时才执行；' +
      '若不唯一会拒绝执行，请到客户端人工处理。<br>' +
      '<b style="color:var(--err)">一键全撤</b>会点击同花顺撤单页的[全撤]，撤销当日<b>全部</b>可撤委托，' +
      '范围仅限"可撤"委托（已成交/已废的不会动），请确认没有正在等待成交的委托后再使用。</div></div>' +
      '<b>重启同花顺</b>：同花顺在运行则重启、未运行则直接启动，用于远程恢复（不必到现场）；' +
      '执行端是否在线与同花顺状态无关，同花顺关掉了也能点。</div></div>' +
    '<div id="ccResult"></div>' +
    '<h3>今日委托</h3>' +
    '<div id="odEntrusts"><div class="empty">点击下方按钮查询</div></div>' +
    '<div style="margin-top:10px"><button class="btn ghost" id="odRefresh">查询当日委托</button></div>';

  $('odBtn').onclick = submitOrder;
  var odCodeEl = $('odCode');
  odCodeEl.onblur = function(){ odCodeEl.value = jqNorm(odCodeEl.value); };
  odCodeEl.onkeydown = function(e){
    if (e.key === 'Enter'){ e.preventDefault(); submitOrder(); }
  };
  $('odRefresh').onclick = function(){ queryInto('entrusts', 'odEntrusts'); };
  $('ccBtn').onclick = async function(){
    var no = $('ccNo').value.trim();
    if (!no){ toast('请输入委托号', 'err'); return; }
    if (!confirm('确认撤销委托 ' + no + ' ？')) return;
    var b = $('ccBtn'); b.disabled = true; b.textContent = '撤单中…';
    $('ccResult').innerHTML = '<div class="card"><div class="loading"><span class="spin"></span>' +
      '已下发撤单任务，等待执行端处理…</div></div>';
    try {
      var d = await post('/cancel', {entrust_no: no});
      $('ccResult').innerHTML = '<div class="card" style="border-color:' +
        (d.ok ? '#bbf7d0' : '#fecaca') + '"><b style="color:var(--' + (d.ok ? 'ok' : 'err') + ')">' +
        (d.ok ? '撤单已完成' : '撤单未完成') + '</b><div style="margin-top:6px">' +
        esc(d.ok ? ((d.task && d.task.message) || '') : (d.error || '')) + '</div></div>';
      toast(d.ok ? '撤单已完成' : '撤单未完成', d.ok ? 'ok' : 'err');
    } catch(e){ toast(e.message, 'err'); }
    finally { b.disabled = false; b.textContent = '撤单'; }
    queryInto('entrusts', 'odEntrusts');
  };
  $('ccaBtn').onclick = async function(){
    if (!confirm('一键全撤将撤销当日【全部】可撤委托！\n\n确认执行？')) return;
    if (!confirm('再次确认：真的要撤销全部可撤委托吗？此操作不可恢复。')) return;
    var b = $('ccaBtn'); b.disabled = true; b.textContent = '全撤中…';
    $('ccResult').innerHTML = '<div class="card"><div class="loading"><span class="spin"></span>' +
      '已下发一键全撤任务，等待执行端处理…</div></div>';
    try {
      var d = await post('/cancel_all', {});
      $('ccResult').innerHTML = '<div class="card" style="border-color:' +
        (d.ok ? '#bbf7d0' : '#fecaca') + '"><b style="color:var(--' + (d.ok ? 'ok' : 'err') + ')">' +
        (d.ok ? '一键全撤已完成' : '一键全撤未完成') + '</b><div style="margin-top:6px">' +
        esc(d.ok ? ((d.task && d.task.message) || '') : (d.error || '')) + '</div></div>';
      toast(d.ok ? '一键全撤已完成' : '一键全撤未完成', d.ok ? 'ok' : 'err');
    } catch(e){ toast(e.message, 'err'); }
    finally { b.disabled = false; b.textContent = '一键全撤'; }
    queryInto('entrusts', 'odEntrusts');
  };
  $('clrBtn').onclick = async function(){
    $('ccResult').innerHTML = '<div class="card"><div class="loading"><span class="spin"></span>已下发清场任务，等待执行端处理…</div></div>';
    try {
      var d = await post('/clear', {});
      $('ccResult').innerHTML = '<div class="card" style="border-color:' +
        (d.ok ? '#bbf7d0' : '#fecaca') + '"><b style="color:var(--' + (d.ok ? 'ok' : 'err') + ')">' +
        (d.ok ? '清场完成' : '清场未完成') + '</b><div style="margin-top:6px">' +
        esc(d.ok ? ((d.task && d.task.message) || '') : (d.error || '')) + '</div></div>';
      toast(d.ok ? '清场完成' : '清场未完成', d.ok ? 'ok' : 'err');
    } catch(e){ toast(e.message, 'err'); }
  };
  $('rsBtn').onclick = async function(){
    if (!confirm('同花顺在运行则重启（会断开当前交易会话，需手动重新登录）；未运行则直接启动。\\n\\n确认继续？')) return;
    $('ccResult').innerHTML = '<div class="card"><div class="loading"><span class="spin"></span>已下发重启任务，等待执行端处理（约 5 秒）…</div></div>';
    try {
      var d = await post('/restart', {});
      $('ccResult').innerHTML = '<div class="card" style="border-color:' +
        (d.ok ? '#bbf7d0' : '#fecaca') + '"><b style="color:var(--' + (d.ok ? 'ok' : 'err') + ')">' +
        (d.ok ? '同花顺已重启（请登录）' : '重启未完成') + '</b><div style="margin-top:6px">' +
        esc(d.ok ? ((d.task && d.task.message) || '') : (d.error || '')) + '</div></div>';
      toast(d.ok ? '同花顺已重启' : '重启未完成', d.ok ? 'ok' : 'err');
    } catch(e){ toast(e.message, 'err'); }
  };
}
async function submitOrder(){
  var body = {
    security: jqNorm($('odCode').value),
    side: $('odSide').value,
    amount: $('odAmount').value.trim(),
    price: $('odPrice').value.trim()
  };
  if (!body.security){ toast('请填写证券代码', 'err'); return; }
  $('odCode').value = body.security;      // 回显补全后的代码，确认框与实际下单保持一致
  var amt = parseInt(body.amount, 10);
  if (!amt || amt <= 0){ toast('数量必须是正整数', 'err'); return; }
  if (!confirm('确认提交：' + (body.side === 'buy' ? '买入' : '卖出') + ' ' +
      body.security + ' ' + amt + ' 股' + (body.price ? ' @ ' + body.price : ' 市价') + ' ？')) return;

  var btn = $('odBtn');
  btn.disabled = true; btn.textContent = '提交中…';
  $('odResult').innerHTML = '<div class="card"><div class="loading"><span class="spin"></span>' +
    '已下发到执行端，等待处理结果（最长约 30 秒，含风控与客户端下单耗时）…</div></div>';
  try {
    var d = await post('/order', body);
    if (!d.ok){
      $('odResult').innerHTML = '<div class="card" style="border-color:#fecaca">' +
        '<b style="color:var(--err)">下单未完成</b><div style="margin-top:6px">' + esc(d.error) + '</div></div>';
      toast(d.error, 'err');
    } else {
      var t = d.task, r = t.result || {};
      var good = t.status === 'done';
      $('odResult').innerHTML = '<div class="card" style="border-color:' + (good ? '#bbf7d0' : '#fecaca') + '">' +
        '<b style="color:' + (good ? 'var(--ok)' : 'var(--err)') + '">' +
        (good ? '下单已提交' : '下单失败') + '</b>' +
        '<div class="kv" style="margin-top:12px">' +
          '<div class="item"><div class="k">任务号</div><div class="v">#' + t.id + '</div></div>' +
          '<div class="item"><div class="k">状态</div><div class="v">' + kindTag(t.status) + '</div></div>' +
          '<div class="item"><div class="k">委托号</div><div class="v" style="font-size:14px">'
            + esc(r.entrust_no || '-') + '</div></div>' +
          '<div class="item"><div class="k">返回信息</div><div class="v" style="font-size:13px">'
            + esc(t.message || '-') + '</div></div>' +
        '</div>' +
        '<div class="note">成交与否请以「账户查询 → 当日委托 / 当日成交」为准。</div></div>';
      toast(good ? '下单已提交' : '下单失败', good ? 'ok' : 'err');
    }
  } catch(e){ toast(e.message, 'err'); }
  finally { btn.disabled = false; btn.textContent = '提交下单'; }
  queryInto('entrusts', 'odEntrusts');
}

/* ==================== 账户查询 ==================== */
var ACCOUNT_TABS = [
  {kind:'balance',   label:'资金'},
  {kind:'positions', label:'持仓'},
  {kind:'entrusts',  label:'当日委托'},
  {kind:'trades',    label:'当日成交'}
];
function pageAccount(){
  $('main').innerHTML = '<h2>账户查询</h2>' +
    '<div class="tabs" id="acTabs">' +
      ACCOUNT_TABS.map(function(t){
        return '<button data-kind="' + t.kind + '">' + t.label + '</button>';
      }).join('') + '</div>' +
    '<div class="flexbetween" style="margin-bottom:12px">' +
      '<span class="note" style="margin:0">数据由执行端实时向券商客户端查询，单次约 3~20 秒。</span>' +
      '<button class="btn ghost" id="acRefresh">刷新</button></div>' +
    '<div id="acBody"><div class="card"><div class="empty">请选择上方标签查询</div></div></div>' +
    '<h3>持仓对账（聚宽 vs 券商）</h3>' +
    '<div id="rcBox"><div class="card"><div class="empty">加载中…</div></div></div>';
  Array.prototype.forEach.call($('acTabs').children, function(b){
    b.onclick = function(){
      Array.prototype.forEach.call($('acTabs').children, function(x){ x.className = ''; });
      b.className = 'on';
      queryInto(b.dataset.kind, 'acBody');
    };
  });
  $('acRefresh').onclick = function(){
    var on = $('acTabs').querySelector('button.on');
    queryInto(on ? on.dataset.kind : 'balance', 'acBody');
  };
  $('acTabs').children[0].className = 'on';
  queryInto('balance', 'acBody');
  loadReconcile();
}
function rcRender(r, autoEnabled, autoAt){
  var colorMap = {ok:'var(--ok)', mismatch:'var(--err)', no_snapshot:'var(--warn)',
                  executor_offline:'var(--warn)', no_broker:'var(--warn)', failed:'var(--err)'};
  var labelMap = {ok:'持仓一致', mismatch:'发现差异', no_snapshot:'无聚宽快照',
                  executor_offline:'执行端离线', no_broker:'券商持仓为空', failed:'对账失败'};
  var h = '';
  if (!r){
    h += '<div class="empty">还没有对账记录。</div>';
  } else {
    var d = r.detail || {};
    var st = r.status;
    h += '<div class="kv" style="margin-bottom:12px">' +
      '<div class="item"><div class="k">最近对账</div><div class="v" style="font-size:14px">' + esc(r.ts) + '</div></div>' +
      '<div class="item"><div class="k">结果</div><div class="v" style="font-size:14px;color:' +
        (colorMap[st] || 'var(--text)') + '">' + (labelMap[st] || st) + '</div></div>' +
      '<div class="item"><div class="k">说明</div><div class="v" style="font-size:13px;font-weight:400">' + esc(d.summary || '') + '</div></div>' +
      '<div class="item"><div class="k">发起方</div><div class="v" style="font-size:14px">' + esc(r.trigger) + '</div></div>' +
    '</div>';
    if (d.items && d.items.length && st === 'mismatch'){
      h += '<div class="scroll"><table><thead><tr><th>代码</th><th>名称</th><th>聚宽持仓</th><th>券商持仓</th><th>差额</th><th>备注</th></tr></thead><tbody>' +
        d.items.map(function(it){
          var bad = it.diff !== 0;
          return '<tr><td>' + esc(it.code) + '</td><td>' + esc(it.name || '-') + '</td>' +
            '<td>' + it.jq + '</td><td>' + it.broker + '</td>' +
            '<td style="color:' + (bad ? 'var(--err);font-weight:600' : 'var(--ok)') + '">' +
            (it.diff > 0 ? '+' : '') + it.diff + '</td>' +
            '<td>' + esc(it.note || '-') + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }
  }
  h += '<div class="flexbetween" style="margin-top:12px">' +
    '<span class="note" style="margin:0">每个交易日 ' + esc(autoAt) + ' 自动对账一次' +
      (autoEnabled ? '' : '（当前已关闭自动对账）') +
      '。聚宽快照由策略 after_market_close 里的 report_positions 上报。</span>' +
    (ME && ME.level >= 2
      ? '<button class="btn" id="rcRun">立即对账</button>'
      : '') +
    '</div>';
  return h;
}
function loadReconcile(){
  var box = $('rcBox');
  api('/reconcile').then(function(d){
    if (!d.ok){ box.innerHTML = '<div class="card" style="border-color:#fecaca">' + esc(d.error) + '</div>'; return; }
    box.innerHTML = '<div class="card">' + rcRender(d.last, d.auto_enabled, d.auto_at) + '</div>';
    var b = $('rcRun');
    if (b) b.onclick = runReconcile;
  }).catch(function(e){ box.innerHTML = '<div class="card" style="border-color:#fecaca">' + esc(e.message) + '</div>'; });
}
async function runReconcile(){
  var b = $('rcRun');
  b.disabled = true; b.textContent = '对账中…';
  $('rcBox').innerHTML = '<div class="card"><div class="loading"><span class="spin"></span>' +
    '正在读取券商持仓并与聚宽快照比对（约 10~40 秒）…</div></div>';
  try {
    var d = await post('/reconcile/run', {});
    var r = d.ok ? d.result : {status:'failed', summary:(d.error || '对账失败')};
    $('rcBox').innerHTML = '<div class="card">' + rcRender(
      {ts:'刚刚', trigger: ME ? ME.username : '-', status:r.status, detail:r},
      true, '') + '</div>';
    var b2 = $('rcRun'); if (b2){ b2.onclick = runReconcile; b2.disabled = false; b2.textContent = '立即对账'; }
    toast(r.summary || (r.status === 'ok' ? '持仓一致' : '对账完成'),
          r.status === 'ok' ? 'ok' : (r.status === 'mismatch' ? 'err' : ''));
  } catch(e){ toast(e.message, 'err'); loadReconcile(); }
}
async function queryInto(kind, targetId){
  var box = $(targetId);
  if (box) box.innerHTML = '<div class="card"><div class="loading"><span class="spin"></span>' +
    '已下发查询任务，等待执行端返回…</div></div>';
  try {
    var d = await api('/account?kind=' + encodeURIComponent(kind));
    if (!box) box = $(targetId);
    if (!d.ok){ box.innerHTML = '<div class="card" style="border-color:#fecaca">' + esc(d.error) + '</div>'; return; }
    var data = d.data;
    if (kind === 'balance'){
      if (!data || !Object.keys(data).length){
        box.innerHTML = '<div class="card"><div class="empty">未获取到资金数据</div></div>'; return;
      }
      var html = '<div class="card"><div class="kv">';
      Object.keys(data).forEach(function(k){
        html += '<div class="item"><div class="k">' + esc(k) + '</div><div class="v">' + num(data[k]) + '</div></div>';
      });
      box.innerHTML = html + '</div><div class="note">查询时间：' + esc(d.finished_at || '') + '</div></div>';
    } else {
      if (!data || !data.length){
        box.innerHTML = '<div class="card"><div class="empty">暂无数据（' + esc(d.message || '') + '）</div></div>';
        return;
      }
      box.innerHTML = '<div class="card" style="padding:0">' + dynTable(data) + '</div>' +
        '<div class="note">共 ' + data.length + ' 条 · 查询时间 ' + esc(d.finished_at || '') + '</div>';
    }
  } catch(e){ if (box) box.innerHTML = '<div class="card" style="border-color:#fecaca">' + esc(e.message) + '</div>'; }
}

/* ==================== 任务队列 ==================== */
async function pageTasks(){
  var box = $('main');
  box.innerHTML = '<h2>任务队列</h2><div class="loading"><span class="spin"></span>加载中…</div>';
  var d = await api('/tasks?limit=50');
  if (!d.ok){ box.innerHTML = '<h2>任务队列</h2><div class="card">' + esc(d.error) + '</div>'; return; }
  var h = '<h2>任务队列</h2><div class="note" style="margin-bottom:10px">' +
    '控制台的每次查询/下单都会生成一条任务，由执行端轮询领取并回报。</div>';
  if (!d.tasks.length){ h += '<div class="card"><div class="empty">暂无任务</div></div>'; box.innerHTML = h; return; }
  var KINDS = {query_balance:'查询资金', query_positions:'查询持仓', query_entrusts:'查询委托',
               query_trades:'查询成交', place_order:'下单', cancel_order:'撤单'};
  h += '<div class="scroll"><table><thead><tr><th>#</th><th>时间</th><th>类型</th><th>发起人</th>' +
       '<th>参数</th><th>状态</th><th>结果</th></tr></thead><tbody>';
  d.tasks.forEach(function(t){
    h += '<tr><td>' + t.id + '</td><td>' + esc(t.created_at) + '</td><td>' +
      esc(KINDS[t.kind] || t.kind) + '</td><td>' + esc(t.operator) + '</td><td><code>' +
      esc(JSON.stringify(t.payload || {})) + '</code></td><td>' + kindTag(t.status) +
      '</td><td>' + esc((t.message || '').slice(0, 90)) + '</td></tr>';
  });
  h += '</tbody></table></div>';
  box.innerHTML = h;
}

/* ==================== 用户管理 ==================== */
async function pageUsers(){
  var box = $('main');
  box.innerHTML = '<h2>用户管理</h2><div class="loading"><span class="spin"></span>加载中…</div>';
  var d = await api('/users');
  if (!d.ok){ box.innerHTML = '<h2>用户管理</h2><div class="card">' + esc(d.error) + '</div>'; return; }
  var h = '<h2>用户管理</h2><div class="card">' +
    '<div class="row">' +
      '<div class="field w2"><label>用户名</label><input id="uName" placeholder="字母/数字/_-"></div>' +
      '<div class="field w2"><label>密码</label><input id="uPass" type="password" placeholder="≥6位"></div>' +
      '<div class="field w2"><label>显示名</label><input id="uDisp" placeholder="可空"></div>' +
      '<div class="field w2"><label>角色</label><select id="uRole">' +
        '<option value="viewer">只读</option>' +
        '<option value="trader">交易员</option>' +
        '<option value="admin">管理员</option></select></div>' +
      '<button class="btn" id="uAdd">新建用户</button>' +
      '<button class="btn danger-solid" id="grBtn" style="margin-left:8px">解除熔断(L3)</button>' +
    '</div>' +
    '<div class="note">只读 viewer：可查看概览、聚宽信号与账户；交易员 trader：可下单；管理员 admin：可管理用户与审计。</div>' +
    '</div><h3>已有用户</h3><div class="scroll"><table><thead><tr>' +
    '<th>#</th><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th>创建时间</th><th>最后登录</th><th>操作</th>' +
    '</tr></thead><tbody>';
  d.users.forEach(function(u){
    h += '<tr><td>' + u.id + '</td><td>' + esc(u.username) + '</td><td>' + esc(u.display_name) +
      '</td><td>' + esc(u.role_label) + '</td><td>' +
      (u.enabled ? '<span class="tag ok">启用</span>' : '<span class="tag bad">禁用</span>') +
      '</td><td>' + esc(u.created_at) + '</td><td>' + esc(u.last_login || '-') +
      '</td><td>' +
        '<button class="btn ghost" data-act="role" data-u="' + esc(u.username) + '">改角色</button> ' +
        '<button class="btn ghost" data-act="pwd" data-u="' + esc(u.username) + '">改密码</button> ' +
        '<button class="btn ' + (u.enabled ? 'danger' : 'ghost') + '" data-act="toggle" data-u="' +
          esc(u.username) + '" data-en="' + (u.enabled ? 1 : 0) + '">' +
          (u.enabled ? '禁用' : '启用') + '</button> ' +
        '<button class="btn danger" data-act="del" data-u="' + esc(u.username) + '">删除</button>' +
      '</td></tr>';
  });
  h += '</tbody></table></div>';
  box.innerHTML = h;

  $('uAdd').onclick = async function(){
    var d2 = await post('/users', {username:$('uName').value.trim(), password:$('uPass').value,
                                   display_name:$('uDisp').value.trim(), role:$('uRole').value});
    toast(d2.ok ? d2.message : d2.error, d2.ok ? 'ok' : 'err');
    if (d2.ok) pageUsers();
  };
  box.querySelectorAll('button[data-act]').forEach(function(b){
    b.onclick = function(){ userAction(b.dataset.act, b.dataset.u, b.dataset.en); };
  });
  var gr = $('grBtn');
  if (gr) gr.onclick = async function(){
    if (!confirm('确认解除执行端 L3 熔断？\\n\\n仅在确认同花顺已恢复正常后操作。')) return;
    var d = await post('/guard/reset', {});
    toast(d.ok ? (d.task && d.task.message || '熔断已恢复') : (d.error || '恢复失败'), d.ok ? 'ok' : 'err');
  };
}
async function userAction(act, name, en){
  if (act === 'del'){
    if (!confirm('确认删除用户 ' + name + ' ？该操作不可恢复。')) return;
    var d = await post('/users/delete', {username:name});
    toast(d.ok ? d.message : d.error, d.ok ? 'ok' : 'err');
    if (d.ok) pageUsers();
  } else if (act === 'toggle'){
    var d2 = await post('/users/update', {username:name, enabled: !parseInt(en, 10)});
    toast(d2.ok ? d2.message : d2.error, d2.ok ? 'ok' : 'err');
    if (d2.ok) pageUsers();
  } else if (act === 'role'){
    var r = prompt('输入新角色：viewer（只读）/ trader（交易员）/ admin（管理员）', 'trader');
    if (!r) return;
    r = r.trim();
    if (['viewer','trader','admin'].indexOf(r) < 0){ toast('角色不合法', 'err'); return; }
    var d3 = await post('/users/update', {username:name, role:r});
    toast(d3.ok ? d3.message : d3.error, d3.ok ? 'ok' : 'err');
    if (d3.ok) pageUsers();
  } else if (act === 'pwd'){
    var p = prompt('输入用户 ' + name + ' 的新密码（至少 6 位）');
    if (!p) return;
    var d4 = await post('/users/password', {username:name, password:p});
    toast(d4.ok ? d4.message : d4.error, d4.ok ? 'ok' : 'err');
  }
}

/* 一键清除全部记录（聚宽信号页 / 审计日志页共用）：清前自动备份数据库，不可恢复 */
async function clearAllRecords(onDone){
  if (!confirm('⚠️ 一键清除全部记录（信号/回报/审计/对账/任务等）。\n清前自动备份数据库到同目录 .bak 文件。\n此操作不可恢复，且会清除审计日志本身（仅留本条清除痕迹）。\n\n确认继续？')) return;
  try {
    var d = await post('/records/clear', {scope:'all'});
    if (d.ok){
      toast('已清除全部记录（备份：' + (d.backup || '-') + '）', 'ok');
      if (onDone) onDone();
    } else toast(d.error || '清除失败', 'err');
  } catch(e){ toast(e.message, 'err'); }
}

/* ==================== 审计日志 ==================== */
async function pageAudit(){
  var box = $('main');
  box.innerHTML = '<h2>审计日志</h2>' +
    (ME && ME.level >= 3 ?
      '<div class="row" style="margin:14px 0">' +
        '<button class="btn danger-solid" id="recClear">一键清除全部记录</button>' +
        '<span class="note" style="margin:0">清前自动备份数据库到同目录 .bak 文件；不可恢复，且会清除本审计日志</span>' +
      '</div>'
    : '') +
    '<div class="loading"><span class="spin"></span>加载中…</div>';
  var d = await api('/audit?limit=200');
  if (!d.ok){ box.innerHTML = '<h2>审计日志</h2><div class="card">' + esc(d.error) + '</div>'; return; }
  var s = d.summary;
  var h = '<h2>审计日志</h2><div class="cards">' +
    '<div class="stat"><div class="k">累计记录</div><div class="v">' + s.total + '</div></div>' +
    '<div class="stat"><div class="k">今日操作</div><div class="v">' + s.today + '</div></div>' +
    '<div class="stat s-err"><div class="k">异常记录</div><div class="v ' + (s.abnormal ? 'err' : '') + '">' +
      s.abnormal + '</div></div>' +
    '</div><div class="scroll"><table><thead><tr>' +
    '<th>时间</th><th>用户</th><th>角色</th><th>IP</th><th>动作</th><th>对象</th><th>详情</th><th>结果</th>' +
    '</tr></thead><tbody>';
  d.logs.forEach(function(l){
    h += '<tr><td>' + esc(l.ts) + '</td><td>' + esc(l.username) + '</td><td>' + esc(l.role) +
      '</td><td>' + esc(l.ip) + '</td><td>' + esc(l.action) + '</td><td>' + esc(l.target) +
      '</td><td>' + esc(l.detail) + '</td><td>' +
      (l.result === 'ok' ? '<span class="tag ok">正常</span>' : '<span class="tag bad">' + esc(l.result) + '</span>') +
      '</td></tr>';
  });
  h += '</tbody></table></div>';
  box.innerHTML = h;
  var recClear = $('recClear');
  if (recClear) recClear.onclick = function(){ clearAllRecords(pageAudit); };
}

/* ==================== 事件绑定 ==================== */
$('lgBtn').onclick = doLogin;
$('lgPass').onkeydown = function(e){ if (e.key === 'Enter') doLogin(); };
$('lgUser').onkeydown = function(e){ if (e.key === 'Enter') $('lgPass').focus(); };
$('outBtn').onclick = async function(){
  await post('/logout', {});
  showLogin();
  toast('已退出登录');
};
$('pwBtn').onclick = async function(){
  var o = prompt('请输入当前密码');
  if (o === null) return;
  var n = prompt('请输入新密码（至少 6 位）');
  if (!n) return;
  var d = await post('/password', {old_password:o, new_password:n});
  toast(d.ok ? d.message : d.error, d.ok ? 'ok' : 'err');
};
window.addEventListener('hashchange', function(){ if (ME) route(); });
(function init(){
  api('/me').then(function(d){
    if (d.ok){ boot(); }
    else {
      showLogin();
      api('/init_hint').then(function(hh){
        if (hh.ok && hh.hint) $('lgHint').innerHTML = hh.hint;
      }).catch(function(){});
    }
  }).catch(function(){ showLogin(); });
})();
</script>
</body>
</html>
"""


def render(version):
    """返回填充了版本号的完整页面。"""
    return PAGE_HTML.replace("__VERSION__", version)
