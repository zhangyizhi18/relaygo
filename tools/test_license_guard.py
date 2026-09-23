# -*- coding: utf-8 -*-
"""
license_guard 离线自测（不连真实授权服务器，全程 mock _post）。
覆盖：机器码账号稳定性、状态机（ok/blocked/grace）、缓存宽限、激活流程、
Flask 全局拦截与白名单、ENABLED=0 旁路。
运行: venv python tools/test_license_guard.py
"""
import base64
import io
import json
import os
import sys
import tempfile
import time
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "relay_server"))

import config                    # noqa: E402
import license_guard as LG       # noqa: E402
from flask import Flask          # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("OK   %s" % name)
    else:
        FAIL += 1
        print("FAIL %s  %s" % (name, detail))


def make_app():
    """挂好守卫路由与拦截的迷你 Flask（不触网，状态由测试手工摆）。"""
    app = Flask(__name__)

    @app.route("/api/signal", methods=["POST"])
    def _sig():
        return '{"ok":true}'

    @app.route("/api/status")
    def _st():
        return '{"ok":true}'

    @app.route("/console/api/order", methods=["POST"])
    def _order():
        return '{"ok":true}'

    @app.route("/")
    def _root():
        return "console"

    @app.route("/console/")
    def _console():
        return "console"

    LG._install_routes(app)
    app.before_request(LG._gate)
    return app


class Env:
    """临时缓存路径 + 配置项保存/恢复。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="jq_license_test_")

    def __enter__(self):
        self._old = (LG._cache_path, config.LICENSE_ENABLED, config.LICENSE_GRACE_HOURS)
        LG._cache_path = lambda: os.path.join(self.tmp, "license_cache.json")
        return self

    def __exit__(self, *a):
        (LG._cache_path, config.LICENSE_ENABLED, config.LICENSE_GRACE_HOURS) = self._old


def ok_result(**kw):
    base = {"valid": True, "message": "授权有效", "expire_date": "2027-01-01 00:00:00",
            "remaining_days": 100, "is_permanent": False, "has_license": True}
    base.update(kw)
    return base


def main():
    print("=" * 62)
    print("license_guard 离线自测")
    print("=" * 62)

    # ---- 1) 机器码授权账号 ----
    acct = LG.machine_account()
    check("1a 授权账号是 8 位纯数字", len(acct) == 8 and acct.isdigit(), acct)
    check("1b 同机器重复计算结果稳定", LG.machine_account() == acct)
    with mock.patch.object(LG, "_machine_raw_id", return_value="win|AAA"):
        a1 = LG.machine_account()
    with mock.patch.object(LG, "_machine_raw_id", return_value="win|BBB"):
        a2 = LG.machine_account()
    check("1c 不同机器码得到不同账号", a1 != a2 and len(a1) == 8, (a1, a2))
    check("1d 账号满足服务端 4-20 位数字要求", 4 <= len(acct) <= 20)

    with Env() as env:

        # ---- 2) refresh 状态机 ----
        with mock.patch.object(LG, "_post", return_value=ok_result()):
            s = LG.refresh("t")
        check("2a 验证通过 -> ok", s == LG.STATE_OK and LG._peek_state() == LG.STATE_OK)
        check("2b 通过后写了缓存", os.path.exists(LG._cache_path()))
        cached = json.load(open(LG._cache_path(), encoding="utf-8"))
        check("2c 缓存绑定本机账号与产品",
              cached.get("account") == acct and cached.get("ea_name") == config.LICENSE_EA_NAME)

        # 明确无效：即使有新鲜缓存也立即拦截（过期不吃宽限）
        with mock.patch.object(LG, "_post",
                               return_value={"valid": False, "message": "授权已过期",
                                             "has_license": False}):
            s = LG.refresh("t")
        check("2d 明确无效(过期) -> blocked（不吃宽限）",
              s == LG.STATE_BLOCKED and "过期" in LG._state["info"].get("message", ""))
        check("2d2 过期原因分类 reason=expired",
              LG._state["info"].get("reason") == "expired")

        # 被禁用同理
        with mock.patch.object(LG, "_post",
                               return_value={"valid": False, "message": "该账号已被禁用",
                                             "is_banned": True}):
            LG.refresh("t")
        check("2e 明确无效(禁用) -> blocked", LG._peek_state() == LG.STATE_BLOCKED)
        check("2e2 禁用原因分类 reason=banned",
              LG._state["info"].get("reason") == "banned")

        # 未激活（服务器可达、明确答复无记录）
        with mock.patch.object(LG, "_post",
                               return_value={"valid": False, "message": "未找到授权记录",
                                             "require_activation": True}):
            LG.refresh("t")
        check("2e3 未激活原因分类 reason=no_license",
              LG._state["info"].get("reason") == "no_license")

        # 网络故障 + 新鲜缓存 -> 宽限
        LG._save_cache(ok_result())                      # ok_time=now
        with mock.patch.object(LG, "_post", side_effect=LG.LicenseServerError("超时")):
            s = LG.refresh("t")
        check("2f 失联+新鲜缓存 -> grace", s == LG.STATE_GRACE)
        check("2f2 失联原因分类 reason=unreachable",
              LG._state["info"].get("reason") == "unreachable")

        check("2g 宽限信息含剩余小时", "小时" in LG._state["info"].get("message", ""))

        # 网络故障 + 过期缓存 -> blocked
        stale = json.load(open(LG._cache_path(), encoding="utf-8"))
        stale["ok_time"] = time.time() - (config.LICENSE_GRACE_HOURS + 5) * 3600
        json.dump(stale, open(LG._cache_path(), "w", encoding="utf-8"))
        with mock.patch.object(LG, "_post", side_effect=LG.LicenseServerError("超时")):
            s = LG.refresh("t")
        check("2h 失联+缓存超宽限期 -> blocked", s == LG.STATE_BLOCKED)

        # 网络故障 + 无缓存 -> blocked
        os.remove(LG._cache_path())
        with mock.patch.object(LG, "_post", side_effect=LG.LicenseServerError("拒连")):
            s = LG.refresh("t")
        check("2i 失联+无缓存 -> blocked", s == LG.STATE_BLOCKED)

        # 换了机器（缓存账号不匹配）-> 视为无缓存
        LG._save_cache(ok_result())
        with mock.patch.object(LG, "machine_account", return_value="99999999"), \
             mock.patch.object(LG, "_post", side_effect=LG.LicenseServerError("拒连")):
            s = LG.refresh("t")
        check("2j 缓存账号与本机不符 -> blocked（防拷贝缓存）", s == LG.STATE_BLOCKED)
        with mock.patch.object(LG, "machine_account", return_value=acct):
            pass

        # ---- 3) 激活流程 ----
        seq = {"use_card": lambda extra=None, timeout=None:
               {"success": True, "message": "激活成功"}}
        with mock.patch.object(LG, "_post",
                               side_effect=lambda action, extra=None, timeout=None:
                               ({"success": True, "message": "激活成功"}
                                if action == "use_card" else ok_result())):
            ok, msg = LG.activate("TEST-CARD-123")
        check("3a 卡密有效 -> 激活成功并转 ok", ok and LG._peek_state() == LG.STATE_OK, msg)

        with mock.patch.object(LG, "_post",
                               side_effect=lambda action, extra=None, timeout=None:
                               {"success": False, "message": "卡密不存在或已使用"}):
            ok, msg = LG.activate("BAD")
        check("3b 卡密无效 -> 失败并返回原因", (not ok) and "卡密" in msg, msg)
        check("3c 卡密失败不改变状态", LG._peek_state() in (LG.STATE_OK, LG.STATE_BLOCKED))

        with mock.patch.object(LG, "_post", side_effect=LG.LicenseServerError("拒连")):
            ok, msg = LG.activate("X")
        check("3d 激活时失联 -> 明确报错不假装成功", (not ok) and "无法连接" in msg)

        ok, msg = LG.activate("  ")
        check("3e 空卡密直接拒绝", not ok)

        # ---- 4) Flask 拦截 ----
        app = make_app()
        c = app.test_client()

        LG._set_state(LG.STATE_BLOCKED, {"message": "未激活"})
        r = c.get("/api/status")
        check("4a blocked 时 /api/status 放行（健康检查）", r.status_code == 200)
        r = c.post("/api/signal", data="{}")
        check("4b blocked 时聚宽信号接口 403",
              r.status_code == 403 and "激活" in (r.get_json() or {}).get("error", ""),
              r.status_code)
        r = c.post("/console/api/order", data="{}")
        check("4c blocked 时控制台下写接口 403", r.status_code == 403)
        r = c.get("/")
        check("4d blocked 时页面 302 跳激活页", r.status_code == 302 and "/license" in r.headers["Location"])
        r = c.get("/license")
        # 激活页是静态 HTML，账号由前端 fetch /license/api/state 填充（4f 已覆盖账号返回）
        check("4e blocked 时激活页 200", r.status_code == 200 and "激活" in r.get_data(as_text=True))
        r = c.get("/license/api/state")
        st = r.get_json()
        check("4f 状态接口返回账号与状态", r.status_code == 200 and st["account"] == acct
              and st["state"] == LG.STATE_BLOCKED)

        LG._set_state(LG.STATE_OK, ok_result())
        r = c.post("/api/signal", data="{}")
        check("4g ok 时信号接口放行", r.status_code == 200)
        r = c.get("/")
        check("4h ok 时页面放行", r.status_code == 200)

        LG._set_state(LG.STATE_GRACE, {"message": "宽限中"})
        r = c.post("/console/api/order", data="{}")
        check("4i grace（失联宽限）时接口放行", r.status_code == 200)

        # 激活接口
        LG._set_state(LG.STATE_BLOCKED, {"message": "未激活"})
        with mock.patch.object(LG, "_post",
                               side_effect=lambda action, extra=None, timeout=None:
                               ({"success": True, "message": "激活成功"}
                                if action == "use_card" else ok_result())):
            r = c.post("/license/api/activate", json={"card_key": "K1"})
        body = r.get_json()
        check("4j 激活接口成功且状态转 ok", r.status_code == 200 and body["ok"]
              and body["state"] == LG.STATE_OK)

        # ---- 5) ENABLED=0 旁路 ----
        config.LICENSE_ENABLED = 0
        app2 = Flask(__name__)

        @app2.route("/api/signal", methods=["POST"])
        def _sig2():
            return '{"ok":true}'

        LG.register(app2, verify_now=False)
        c2 = app2.test_client()
        r = c2.post("/api/signal", data="{}")
        check("5a ENABLED=0 时 register 不装拦截，接口直通", r.status_code == 200)
        check("5b ENABLED=0 时状态为 disabled", LG._peek_state() == LG.STATE_DISABLED)
        config.LICENSE_ENABLED = 1

        # ---- 6) snapshot 不泄露内部字段 ----
        LG._set_state(LG.STATE_OK, {"message": "授权有效", "expire_date": "2027-01-01",
                                    "remaining_days": 100, "card_key": "SECRET"})
        snap = LG.snapshot()
        check("6a snapshot 不含 card_key", "card_key" not in snap and "SECRET" not in json.dumps(snap))
        check("6b snapshot 含账号/到期/剩余天数",
              snap["account"] == acct and snap["expire_date"] == "2027-01-01"
              and snap["remaining_days"] == 100)

        # ---- 7) 非 JSON 响应要报友好错误（用户截图里的裸异常） ----
        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"<html>502 Bad Gateway</html>"

        class _FakeOpener:
            def open(self, req, timeout=None):
                return _FakeResp()

        with mock.patch.object(LG.urllib.request, "build_opener",
                               return_value=_FakeOpener()):
            try:
                LG._post("verify")
                friendly = ""
            except LG.LicenseServerError as e:
                friendly = str(e)
        check("7a 服务端返回非 JSON -> 友好提示而非裸异常",
              "响应异常" in friendly and "JSON" in friendly, friendly)

        # ---- 8) MT5LicenseWeb2.4 原生加密协议（2026-09-13 实测：其所有响应
        #         均经 simple_encrypt(XOR+Base64) 加密，请求侧才是明文） ----
        def _enc(obj):
            key = LG.ENCRYPT_KEY.encode("utf-8")
            data = json.dumps(obj, ensure_ascii=True).encode("utf-8")
            return base64.b64encode(
                bytes(b ^ key[i % len(key)] for i, b in enumerate(data)))

        class _FakeEncResp(_FakeResp):
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

        class _FakeEncOpener:
            def __init__(self, body):
                self._body = body

            def open(self, req, timeout=None):
                return _FakeEncResp(self._body)

        ok = {"valid": True, "message": "验证成功", "expire_date": "2027-01-01",
              "remaining_days": 100, "is_permanent": False, "has_license": True}
        with mock.patch.object(LG.urllib.request, "build_opener",
                               return_value=_FakeEncOpener(_enc(ok))):
            r = LG._post("verify")
        check("8a 加密响应自动解密并解析", r.get("valid") is True, r)

        # 8b 错误响应（400/401）也走加密通道，能解出可读 message
        class _Fake401Opener:
            def __init__(self, body):
                self._body = body

            def open(self, req, timeout=None):
                raise LG.urllib.error.HTTPError(
                    req.full_url, 401, "Unauthorized",
                    {"Content-Type": "application/json"},
                    io.BytesIO(self._body))

        with mock.patch.object(LG.urllib.request, "build_opener",
                               return_value=_Fake401Opener(_enc(
                                   {"error": "APIKEY认证失败",
                                    "message": "未授权访问，请检查APIKEY配置"}))):
            try:
                LG._post("verify")
                friendly = ""
            except LG.LicenseServerError as e:
                friendly = str(e)
        check("8b 加密的 401 错误体能解出可读提示",
              "401" in friendly and "APIKEY" in friendly, friendly)

        # 8c 明文 JSON 兼容（自定义服务端/直连明文）
        with mock.patch.object(LG.urllib.request, "build_opener",
                               return_value=_FakeEncOpener(
                                   json.dumps(ok).encode("utf-8"))):
            r = LG._post("verify")
        check("8c 明文 JSON 响应仍然兼容", r.get("valid") is True)

        # 8d _decrypt_body 直接断言三分支
        check("8d 解密函数：密文/明文/垃圾 各归其位",
              LG._decrypt_body(_enc(ok)).get("valid") is True
              and LG._decrypt_body(b'{"valid":true}').get("valid") is True
              and LG._decrypt_body(b"garbage!!!") is None
              and LG._decrypt_body(b"") is None)

        # ---- 9) 正式版锁：release.lock 存在时 RELAY_LICENSE_ENABLED=0 强制无效 ----
        import subprocess
        relay_dir = os.path.join(ROOT, "relay_server")
        lock_path = os.path.join(relay_dir, "release.lock")
        probe = ("import sys; sys.path.insert(0, r'%s'); import config; "
                 "print(config.LICENSE_ENABLED, config.LICENSE_SWITCH_LOCKED)" % relay_dir)
        base_env = dict(os.environ, RELAY_LICENSE_ENABLED="0",
                        JQ_ENVFILE_NOCREATE="1", PYTHONIOENCODING="utf-8")
        try:
            # 9a 无锁（开发仓库默认）：0 生效
            out = subprocess.run([sys.executable, "-X", "utf8", "-c", probe],
                                 capture_output=True, text=True, env=base_env,
                                 timeout=60).stdout.strip().splitlines()[-1]
            check("9a 开发仓库无锁：ENABLED=0 正常生效", out == "0 False", out)

            # 9b 有锁（模拟发布包）：0 被强制改回 1
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write("test lock\n")
            out = subprocess.run([sys.executable, "-X", "utf8", "-c", probe],
                                 capture_output=True, text=True, env=base_env,
                                 timeout=60).stdout.strip().splitlines()[-1]
            check("9b release.lock 存在：ENABLED=0 被强制无效（锁生效）",
                  out == "1 True", out)
        finally:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        check("9c 测试后锁文件已清理（不污染开发环境）", not os.path.exists(lock_path))

        # ---- 10) 多 worker 激活同步（gunicorn --workers 2：用缓存 mtime 当跨进程信号）----
        # 场景：worker B 处于 blocked（无缓存），worker A 激活成功写了缓存。
        # B 的 gate 应发现缓存出现 -> 立即复验 -> ok，而不是等 6h 复验线程。
        if os.path.exists(LG._cache_path()):
            os.remove(LG._cache_path())     # 模拟全新部署（前面用例写过缓存）
        LG._cache_mtime_seen = None      # 模拟 worker 刚起
        with mock.patch.object(LG, "_post",
                               return_value={"valid": False, "message": "未找到授权记录",
                                             "require_activation": True}):
            LG.refresh("t")
        check("10a 摆位：worker B 为 blocked 且无缓存",
              LG._peek_state() == LG.STATE_BLOCKED and not os.path.exists(LG._cache_path()))
        app2 = make_app()
        c2 = app2.test_client()
        r = c2.get("/console/")          # 首次 gate：记基线（无缓存 -> 0.0）
        check("10b 基线请求仍拦截", r.status_code == 302)
        LG._save_cache(ok_result())      # 模拟 worker A 激活成功后写缓存
        with mock.patch.object(LG, "_post", return_value=ok_result()):
            r = c2.get("/console/")      # gate 检测到缓存出现 -> 立即复验 -> 放行
        check("10c 另一 worker 激活后，本 worker 下一次请求即放行（免重启）",
              r.status_code == 200 and LG._peek_state() == LG.STATE_OK,
              (r.status_code, LG._peek_state()))

        # 10d 缓存未再变化时不重复复验（不刷服务器）
        calls = []

        def _counting_post(action, extra=None, timeout=None):
            calls.append(action)
            return ok_result()

        with mock.patch.object(LG, "_post", side_effect=_counting_post):
            c2.get("/console/")
            n1 = len(calls)
            c2.get("/console/")
        check("10d 缓存未变化时不重复复验", n1 == 0 and len(calls) == 0, (n1, len(calls)))

        # 10e 缓存被删（如手工清理）不误伤：状态保持 ok，gate 正常放行
        os.remove(LG._cache_path())
        r = c2.get("/console/")
        check("10e 缓存消失不影响已 ok 的 worker", r.status_code == 200 and LG._peek_state() == LG.STATE_OK)
        LG._cache_mtime_seen = None      # 还原，防影响其他用例

    print("=" * 62)
    print("结果: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
