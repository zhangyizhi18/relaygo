# -*- coding: utf-8 -*-
"""
RelayGo · 中转服务授权守卫（非侵入式模块，2026-09-12）

对接 MT5LicenseWeb2.4.py 授权服务端（POST /api/license + 头 X-API-Key，默认端口 9527）。
app.py 只需两行接入：
    import license_guard
    license_guard.register(app)

行为约定（用户 2026-09-12 拍板）：
1. 运行期持续拦：启动时验证一次 + 后台线程每 RELAY_LICENSE_RECHECK 秒复验 +
   before_request 全局守门（拦 /api/signal、执行端通道、控制台全部接口）。
2. 授权账号 = 本机机器码派生的 8 位纯数字，固定不可改（不提供配置项）；
   激活页上展示，用户把账号报给管理员生成卡密，再用卡密激活。
3. 未授权时中转服务照常启动，但除激活页 /license 与健康检查 /api/status 外，
   API 一律 403、页面一律跳转激活页——引导激活而不是拒绝启动。
4. API-Key 与服务端 MT5LicenseWeb2.4.py 硬编码一致（RELAY_LICENSE_API_KEY 可覆盖）。

失败语义（宁可持续可用，也不让授权服务器成为下单单点故障）：
- 明确无效（未激活/已过期/被禁用）→ 立即拦截，不吃宽限；
- 服务器失联/超时 → 离线宽限期：本地缓存的上次成功验证在
  RELAY_LICENSE_GRACE_HOURS（默认 72h）内则放行（state=grace），超时才拦截；
- 激活成功立即放行，无需重启。

依赖：仅标准库（urllib/winreg/hashlib），零第三方依赖，exe 与 docker 通用。
"""
import base64
import hashlib
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

from flask import jsonify, redirect, request

import config

try:                                    # Windows 读 MachineGuid（标准库）
    import winreg
except ImportError:                     # Linux/docker
    winreg = None

log = logging.getLogger("relay.license")

# ---------------------------------------------------------------- 状态
STATE_OK = "ok"                # 已授权
STATE_GRACE = "grace"          # 服务器失联，离线宽限期内
STATE_BLOCKED = "blocked"      # 未激活/已过期/被禁用/宽限期已过
STATE_DISABLED = "disabled"    # RELAY_LICENSE_ENABLED=0，功能整体关闭

_state_lock = threading.Lock()
_state = {"state": STATE_BLOCKED, "info": {}, "checked_at": 0.0}
_thread_started = False
_cache_mtime_seen = None   # 本 worker 上次见到的缓存文件 mtime（跨 worker 激活同步信号）

# 激活页/健康检查之外全部拦截的白名单前缀
_ALLOWED_PREFIXES = ("/license",)
_ALLOWED_EXACT = ("/api/status", "/favicon.ico")


def snapshot():
    """当前授权状态（供 /api/status 与激活页轮询）。只暴露必要字段。"""
    with _state_lock:
        info = dict(_state["info"])
    info.pop("card_key", None)
    return {
        "state": _peek_state(),
        "reason": info.get("reason", ""),
        "account": machine_account(),
        "ea_name": config.LICENSE_EA_NAME,
        "message": info.get("message", ""),
        "expire_date": info.get("expire_date", ""),
        "remaining_days": info.get("remaining_days"),
        "is_permanent": bool(info.get("is_permanent")),
    }


def _peek_state():
    with _state_lock:
        return _state["state"]


def _set_state(state, info=None):
    with _state_lock:
        _state["state"] = state
        _state["info"] = dict(info or {})
        _state["checked_at"] = time.time()


# ---------------------------------------------------------------- 机器码 -> 8 位授权账号
_SALT = "RelayGo|license|v1"


def _machine_raw_id():
    """取稳定的机器原始标识：Windows 用注册表 MachineGuid，
    Linux 用 /etc/machine-id，都没有再用 主机名+MAC 兜底。"""
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                val = winreg.QueryValueEx(k, "MachineGuid")[0]
                if val:
                    return "win|" + str(val)
        except OSError:
            pass
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return "nix|" + val
        except OSError:
            continue
    return "fallback|%s|%s" % (socket.gethostname(), uuid.getnode())


def machine_account():
    """机器码 -> 8 位纯数字授权账号（固定、不可配置）。"""
    raw = _machine_raw_id()
    digest = hashlib.sha256((_SALT + "|" + raw).encode("utf-8")).hexdigest()
    return "%08d" % (int(digest, 16) % 100000000)


# ---------------------------------------------------------------- 本地缓存（离线宽限依据）
def _cache_path():
    return os.path.join(os.path.dirname(config.DB_PATH), "license_cache.json")


def _load_cache():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("account") == machine_account() \
                and data.get("ea_name") == config.LICENSE_EA_NAME:
            return data
    except (OSError, ValueError):
        pass
    return None


def _save_cache(result):
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump({
                "account": machine_account(),
                "ea_name": config.LICENSE_EA_NAME,
                "ok_time": time.time(),
                "expire_date": result.get("expire_date", ""),
            }, f, ensure_ascii=False)
    except OSError as e:
        log.warning("授权缓存写入失败（不影响验证）: %s", e)


# ---------------------------------------------------------------- 授权服务端 HTTP
class LicenseServerError(Exception):
    """无法从授权服务器得到明确结论（网络/超时/5xx）。"""


# 与授权服务端 MT5LicenseWeb2.4.py 硬编码一致：其 /api/license 的所有响应
# （含 400/401 错误）都经 _send_json -> simple_encrypt（XOR+Base64）加密，
# 请求侧才是明文 JSON。这里必须先解密再解析。
ENCRYPT_KEY = "1aLaQJdam9KoTp7iQoS1Xv8FtBt53sxD9"


def _decrypt_body(raw):
    """按服务端协议解析响应体：明文 JSON 优先，其次 XOR+Base64 密文。

    返回 dict；两种都不是时返回 None（调用方报"响应异常"）。"""
    if not raw:
        return None
    # 1) 明文 JSON（兼容自定义/改造过的服务端）
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except ValueError:
        pass
    # 2) MT5LicenseWeb2.4 原生协议：Base64 -> XOR(ENCRYPT_KEY)
    try:
        enc = base64.b64decode(raw.strip(), validate=True)
        key = ENCRYPT_KEY.encode("utf-8")
        plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(enc))
        v = json.loads(plain.decode("utf-8"))
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _post(action, extra=None, timeout=None):
    """POST /api/license。返回解析后的 dict；网络问题抛 LicenseServerError。
    注意：4xx 属于服务器给了明确答复（比如 Invalid JSON/参数问题），原样返回。"""
    payload = {"action": action,
               "mt5_account": machine_account(),
               "ea_name": config.LICENSE_EA_NAME}
    if extra:
        payload.update(extra)
    req = urllib.request.Request(
        config.LICENSE_URL.rstrip("/") + "/api/license",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-API-Key": config.LICENSE_API_KEY,
                 # 线上地址经 Cloudflare 代理时，默认 Python UA 会触发
                 # Bot 防护 error 1010（浏览器签名拦截），须用浏览器 UA
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/126.0.0.0 Safari/537.36"},
        method="POST")
    try:
        # 显式绕过系统代理：授权验证走直连，避免本机代理软件把请求劫持成 502
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout or config.LICENSE_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # 4xx/5xx 的错误体也是加密 JSON（_send_auth_error/_send_json 同一通道），
        # 解开里面的 message 给出可读错误
        try:
            body = e.read()
        except Exception:
            body = b""
        parsed = _decrypt_body(body.decode("utf-8", errors="replace"))
        detail = (parsed or {}).get("message") or (parsed or {}).get("error")
        raise LicenseServerError(
            "授权服务返回 HTTP %s%s" % (e.code, "（%s）" % detail if detail else ""))
    except Exception as e:
        raise LicenseServerError("无法连接授权服务器（%s）" % getattr(e, "reason", e))
    parsed = _decrypt_body(raw.decode("utf-8", errors="replace"))
    if parsed is None:
        # 服务端有响应但按两种协议都解析不出：多半是地址指错（反代/别的服务占用该端口）
        raise LicenseServerError("授权服务响应异常（返回内容不是 JSON，请确认地址指向 MT5LicenseWeb2.4 授权服务端）")
    return parsed


# ---------------------------------------------------------------- 状态判定
def _classify_reject(result):
    """把服务端的明确拒绝分成三类原因（激活页分区展示用）。"""
    if result.get("is_banned"):
        return "banned"
    if "过期" in str(result.get("message", "")):
        return "expired"
    return "no_license"


def refresh(reason="periodic"):
    """向授权服务器验证一次并更新全局状态。返回新状态字符串。"""
    now = time.time()
    try:
        result = _post("verify")
    except LicenseServerError as e:
        cached = _load_cache()
        if cached:
            age_h = (now - float(cached.get("ok_time", 0))) / 3600.0
            if 0 <= age_h <= config.LICENSE_GRACE_HOURS:
                _set_state(STATE_GRACE, {
                    "reason": "unreachable",
                    "message": "授权服务器暂不可达，宽限期内正常使用（剩余 %.1f 小时）"
                               % (config.LICENSE_GRACE_HOURS - age_h),
                    "expire_date": cached.get("expire_date", ""),
                })
                log.warning("[%s] 授权服务器不可达（%s），进入离线宽限期", reason, e)
                return STATE_GRACE
        _set_state(STATE_BLOCKED, {
            "reason": "unreachable",
            "message": "无法连接授权服务器，且无有效授权缓存：%s" % e})
        log.error("[%s] 授权验证失败且无可用缓存: %s", reason, e)
        return STATE_BLOCKED

    if result.get("valid"):
        _save_cache(result)
        _set_state(STATE_OK, result)
        log.info("[%s] 授权有效，到期 %s（剩余 %s 天）",
                 reason, result.get("expire_date", "?"), result.get("remaining_days", "?"))
        return STATE_OK

    # 明确无效：未激活 / 已过期 / 被禁用 —— 立即拦截，不吃宽限
    result["reason"] = _classify_reject(result)
    _set_state(STATE_BLOCKED, result)
    log.warning("[%s] 授权无效(%s): %s", reason, result["reason"],
                result.get("message", "?"))
    return STATE_BLOCKED


def activate(card_key):
    """卡密激活。返回 (ok, message)。成功后立即复验并放行。"""
    card_key = str(card_key or "").strip()
    if not card_key:
        return False, "请填写卡密"
    try:
        result = _post("use_card", {"card_key": card_key})
    except LicenseServerError as e:
        return False, "授权服务器无法连接：%s" % e
    ok = bool(result.get("success"))
    msg = str(result.get("message", ""))
    if ok:
        state = refresh("activate")
        if state != STATE_OK:
            return False, "卡密已激活但验证未通过（%s），请联系管理员" % state
        return True, msg or "激活成功"
    return False, msg or "卡密无效"


# ---------------------------------------------------------------- Flask 接入
_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RelayGo · 授权激活</title>
<style>
 body{font-family:"Microsoft YaHei",sans-serif;background:#f0f2f5;margin:0;
      display:flex;align-items:center;justify-content:center;min-height:100vh}
 .card{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);
       padding:36px 40px;width:400px;max-width:92vw}
 h1{font-size:20px;margin:0 0 6px;color:#1f2329}
 .sub{color:#8a919f;font-size:13px;margin-bottom:22px}
 label{display:block;font-size:13px;color:#4e5969;margin:14px 0 6px}
 .acct{display:flex;gap:8px}
 .acct input{flex:1;background:#f7f8fa;border:1px solid #e5e6eb;border-radius:6px;
             padding:10px 12px;font-size:18px;letter-spacing:6px;font-weight:700;
             color:#1f2329;text-align:center}
 button{width:100%;margin-top:18px;background:#3370ff;border:none;border-radius:6px;
        padding:11px;font-size:15px;color:#fff;cursor:pointer}
 button:disabled{background:#94bfff;cursor:not-allowed}
 input.cardkey{width:100%;box-sizing:border-box;border:1px solid #e5e6eb;border-radius:6px;
               padding:10px 12px;font-size:15px}
 .msg{margin-top:14px;font-size:13px;min-height:18px}
 .msg.err{color:#d83931}.msg.ok{color:#00a854}.msg.info{color:#8a919f}
 .banner{display:none;border-radius:8px;padding:10px 12px;font-size:13px;
         line-height:1.6;margin:0 0 14px}
 .banner.red{display:block;background:#fdecec;color:#d83931;border:1px solid #f5c8c8}
 .banner.orange{display:block;background:#fff7e8;color:#b26b00;border:1px solid #f5dfb8}
 .banner.blue{display:block;background:#eaf3ff;color:#1d5fd1;border:1px solid #c6dcfb}
 .banner.green{display:block;background:#e8f7ef;color:#00875a;border:1px solid #bfe8d2}
 .state{margin-top:10px;font-size:12px;color:#8a919f;word-break:break-all}
</style></head><body><div class="card">
 <h1>RelayGo 中转服务 · 授权激活</h1>
 <div class="sub">本机授权账号由机器码自动生成，请把账号发给管理员领取卡密</div>
 <div id="banner" class="banner"></div>
 <label>本机授权账号（8 位数字，不可修改）</label>
 <div class="acct"><input id="acct" readonly></div>
 <label>卡密</label>
 <input id="ck" class="cardkey" placeholder="粘贴管理员发放的卡密" autocomplete="off">
 <button id="go" onclick="doAct()">激 活</button>
 <div id="msg" class="msg info"></div>
 <div id="state" class="state"></div>
</div>
<script>
function show(el,cls,txt){var m=document.getElementById(el);m.className=cls;m.textContent=txt;}
function banner(cls,txt){var b=document.getElementById('banner');
 b.className='banner '+cls;b.textContent=txt;}
function loadState(){
 fetch('/license/api/state').then(function(r){return r.json()}).then(function(d){
  document.getElementById('acct').value=d.account||'';
  var s=d.state, r=d.reason||'';
  if(s==='ok'){banner('green','已授权（到期 '+(d.expire_date||'-')+'），正在进入控制台…');
   setTimeout(function(){location.href='/console/'},1200);return;}
  if(s==='grace'){banner('blue','授权服务器暂时无法连接，离线宽限中（不影响使用）。'+(d.message||''));
   return;}
  if(r==='unreachable'){
   banner('red','无法连接授权服务器，激活功能暂不可用。请检查本机网络，或联系管理员确认授权服务端状态。');
   return;}
  if(r==='expired'){banner('orange','授权已过期，请向管理员续期后重新激活。');return;}
  if(r==='banned'){banner('red','该授权账号已被禁用，请联系管理员。');return;}
  banner('orange','本机尚未激活。请把上方授权账号发给管理员领取卡密，在下方输入激活。');
 }).catch(function(){banner('red','无法连接本机中转服务，请确认服务正在运行')});
}
function doAct(){
 var ck=document.getElementById('ck').value.trim();
 if(!ck){show('msg','msg err','请填写卡密');return;}
 var b=document.getElementById('go');b.disabled=true;b.textContent='正在激活…';
 fetch('/license/api/activate',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({card_key:ck})})
 .then(function(r){return r.json()}).then(function(d){
  b.disabled=false;b.textContent='激 活';
  if(d.ok){show('msg','msg ok',d.message||'激活成功');loadState();}
  else{show('msg','msg err',d.message||'激活失败');loadState();}
 }).catch(function(){b.disabled=false;b.textContent='激 活';
  show('msg','msg err','网络错误，请重试')});
}
loadState();
setInterval(loadState,5000);   // 状态自动刷新：服务器恢复/管理员激活后页面自动翻转
</script></body></html>"""


def _install_routes(app):
    @app.route("/license", methods=["GET"])
    def _license_page():
        return _PAGE

    @app.route("/license/api/state", methods=["GET"])
    def _license_state():
        return jsonify(ok=True, **snapshot())

    @app.route("/license/api/activate", methods=["POST"])
    def _license_activate():
        data = request.get_json(silent=True) or {}
        ok, msg = activate(data.get("card_key", ""))
        return jsonify(ok=ok, message=msg, state=_peek_state())


def _sync_if_cache_changed():
    """多 worker 部署（gunicorn --workers 2）下的跨进程激活同步。

    激活请求只落在处理它的那个 worker：它 refresh 成功会写 license_cache.json，
    其它 worker 的内存状态仍是 blocked，要等 6h 复验线程才翻转——docker 里的
    表现就是"激活后要重启容器才生效"。这里用缓存文件 mtime 当变更信号：
    本 worker 状态非 ok 且缓存 mtime 变化（另一个 worker 刚激活/验证通过），
    立即复验一次，让激活秒级全实例生效。
    """
    global _cache_mtime_seen
    try:
        m = os.path.getmtime(_cache_path())
    except OSError:
        m = 0.0                        # 文件不存在记 0（与"出现了文件"可区分）
    if _cache_mtime_seen is None:
        _cache_mtime_seen = m          # 本 worker 首见，记基线
        return
    if m != _cache_mtime_seen:
        _cache_mtime_seen = m
        if _peek_state() != STATE_OK:
            log.info("检测到授权缓存已更新（另一 worker 激活/验证通过），立即复验")
            refresh("cache-sync")


def _gate():
    """before_request 全局守门。返回 None=放行，否则为拦截响应。"""
    state = _peek_state()
    if state not in (STATE_OK, STATE_DISABLED):
        _sync_if_cache_changed()
        state = _peek_state()
    if state in (STATE_OK, STATE_GRACE, STATE_DISABLED):
        return None
    path = request.path
    if path in _ALLOWED_EXACT or any(path.startswith(p) for p in _ALLOWED_PREFIXES):
        return None
    if "/api/" in path:
        return jsonify(ok=False,
                       error="中转服务未激活或授权已失效，请在浏览器打开本服务首页完成卡密激活"), 403
    return redirect("/license")


def _loop():
    while True:
        time.sleep(max(60, int(config.LICENSE_RECHECK_SECONDS)))
        try:
            refresh("recheck")
        except Exception as e:          # refresh 内部已兜底，这里防意外
            log.error("授权复验线程异常: %s", e)


def register(app, verify_now=True):
    """把授权守卫挂到 Flask app 上（幂等：重复调用只生效一次）。"""
    global _thread_started
    if not config.LICENSE_ENABLED:
        _set_state(STATE_DISABLED, {"message": "授权验证未启用"})
        log.info("授权验证未启用（RELAY_LICENSE_ENABLED=0），跳过")
        return

    _install_routes(app)
    app.before_request(_gate)

    if verify_now:
        refresh("startup")
    if not _thread_started:
        _thread_started = True
        t = threading.Thread(target=_loop, name="license-recheck", daemon=True)
        t.start()
    log.info("授权守卫已挂载：账号=%s 产品=%s 服务端=%s",
             machine_account(), config.LICENSE_EA_NAME, config.LICENSE_URL)
