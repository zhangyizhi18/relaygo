# -*- coding: utf-8 -*-
"""
包自检 —— 不靠肉眼，逐项断言本次发版产物是否合规。

用法:
    python release/verify_release.py
（build_all.py 会在最后自动调用它）
退出码非 0 表示有断言失败，发版不算成功。
"""
import importlib.util
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_common as rc  # noqa: E402

ROOT = rc.ROOT
TS = rc.release_ts()
OUT = rc.out_dir(TS)

RELAY_EXE = os.path.join(OUT, rc.package_name("relaygo-relay", TS))
EXEC_EXE = os.path.join(OUT, rc.package_name("relaygo-executor", TS))
DOCKER_ZIP = os.path.join(OUT, rc.package_name("relaygo-relay-docker", TS, ext="zip", platform=None))
MANUAL = os.path.join(OUT, "用户手册-%s-%s.html" % (rc.VER, TS))
NOTES = os.path.join(OUT, "升级说明-%s-%s.txt" % (rc.VER, TS))
MD5F = os.path.join(OUT, "md5.txt")
TPL_RELAY = os.path.join(OUT, "中转服务配置模板-%s-%s.env.example" % (rc.VER, TS))
TPL_EXEC = os.path.join(OUT, "执行端配置模板-%s-%s.env.example" % (rc.VER, TS))
BAT_RELAY = os.path.join(OUT, "中转服务-配置.bat")
BAT_EXEC = os.path.join(OUT, "执行端-配置.bat")

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("OK " if ok else "FAIL", name, ("  -> " + detail) if detail and not ok else ""))


def main():
    print("包自检  版本=%s  时间戳=%s\n目录=%s\n" % (rc.VER, TS, OUT))

    # ---- 1. 结构 ----
    print("[结构]")
    check("中转 exe 存在", os.path.exists(RELAY_EXE))
    check("执行端 exe 存在", os.path.exists(EXEC_EXE))
    check("docker 源码包存在", os.path.exists(DOCKER_ZIP))
    check("用户手册存在", os.path.exists(MANUAL))
    check("升级说明存在", os.path.exists(NOTES))
    check("中转 exe 体积合理(>5MB)", os.path.exists(RELAY_EXE) and os.path.getsize(RELAY_EXE) > 5 << 20)
    check("执行端 exe 体积合理(>20MB)", os.path.exists(EXEC_EXE) and os.path.getsize(EXEC_EXE) > 20 << 20)

    # ---- 2. 版本一致性 ----
    print("[版本一致性]")
    rel_ver = rc.VER
    exec_ver = _read_executor_version()
    check("中转/执行端版本号一致 (%s)" % rel_ver, rel_ver == exec_ver, "执行端=%s" % exec_ver)
    if os.path.exists(MANUAL):
        m = open(MANUAL, encoding="utf-8").read()
        check("用户手册含版本号 %s" % rel_ver, rel_ver in m)
    if os.path.exists(NOTES):
        n = open(NOTES, encoding="utf-8").read()
        check("升级说明含版本号 %s" % rel_ver, rel_ver in n)

    # ---- 3. docker 包内容 & 洁净度 ----
    print("[docker 源码包]")
    if os.path.exists(DOCKER_ZIP):
        names = zipfile.ZipFile(DOCKER_ZIP).namelist()
        joined = "\n".join(names)
        for must in ("docker-compose.yml", "relay_server/Dockerfile",
                     "relay_server/app.py", "relay_server/web_ui.py",
                     "relay_server/web_console.py", "relay_server/store_web.py",
                     "relay_server/task_api.py", "relay_server/envfile.py",
                     "relay_server/env_template.py",
                     "relay_server/reconcile.py", "relay_server/security_code.py",
                     "relay_server/license_guard.py"):
            check("包含 %s" % must, any(n.endswith(must) for n in names))
        check("包含 relay_server/release.lock（正式版授权锁）",
              any(n.endswith("relay_server/release.lock") for n in names))
        check("不含 __pycache__", "__pycache__" not in joined)
        check("不含 .db 数据文件", ".db" not in joined)
        ui = _zip_read(DOCKER_ZIP, "web_ui.py")
        check("web_ui.py 含控制台特征串", "RelayGo" in ui and "控制台" in ui)
        cfg = _zip_read(DOCKER_ZIP, "config.py")
        check("包内 config.py 含正式版锁逻辑", "LICENSE_SWITCH_LOCKED" in cfg)

    # ---- 3.5 配置模板与双击配置 bat（.env 配置体系）----
    print("[配置模板]")
    for path, label in ((TPL_RELAY, "中转配置模板"), (TPL_EXEC, "执行端配置模板"),
                        (BAT_RELAY, "中转配置bat"), (BAT_EXEC, "执行端配置bat")):
        check("%s 存在: %s" % (label, os.path.basename(path)), os.path.exists(path))
    for tpl, cfg_dir, prefix in ((TPL_RELAY, "relay_server", "RELAY_"),
                                 (TPL_EXEC, "executor", "EXECUTOR_")):
        if os.path.exists(tpl):
            body = open(tpl, encoding="utf-8-sig").read()
            # 模板里的键必须都真实存在于对应 config.py（防止模板与代码漂移）
            bad = _template_keys(tpl, cfg_dir, prefix)
            check("%s 键名与 config.py 一致" % cfg_dir, not bad, "多余键: %s" % bad)
            # 模板允许出现文档化的默认演示密钥，但绝不能有"非默认"的真实密钥
            real = _real_secrets(body)
            check("%s 不含非默认的真实密钥" % cfg_dir, not real, "可疑值: %s" % real)
            check("%s 为 UTF-8 带 BOM（记事本中文不乱码）" % cfg_dir,
                  open(tpl, "rb").read(3) == b"\xef\xbb\xbf")

    # ---- 4. md5 一致性 ----
    print("[md5]")
    if os.path.exists(MD5F):
        recorded = {}
        for line in open(MD5F, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            digest, fn = line.split(None, 1)
            recorded[fn.strip()] = digest
        for fn in (os.path.basename(RELAY_EXE), os.path.basename(EXEC_EXE),
                   os.path.basename(DOCKER_ZIP)):
            p = os.path.join(OUT, fn)
            if os.path.exists(p) and fn in recorded:
                check("md5 与文件一致: %s" % fn, recorded[fn] == rc.md5(p))
            else:
                check("md5 记录存在: %s" % fn, fn in recorded)

    failed = [n for n, ok, _ in _results if not ok]
    print("\n结果: %d 项通过, %d 项失败" % (len(_results) - len(failed), len(failed)))
    if failed:
        print("失败项: " + ", ".join(failed))
        sys.exit(1)
    print("全部通过 ✓")


_ALLOWED_DEFAULTS = {
    "jq-signal-key-2026-change-me",
    "executor-key-2026-change-me",
    "relay-web-secret-change-me-2026",
    "请改成一段随机字符串-用于登录会话签名",
    "请改成你的管理员密码",
    "admin123",
}


def _real_secrets(template_text):
    """返回模板中"看起来像真实密钥、又不等于已知默认演示值"的值列表。

    只认明确的密钥特征（key-2026 / password / admin123），
    不做"长字符串"猜测——否则 URL、占位符都会误报。
    """
    suspects = []
    for line in template_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        if not value:
            continue
        looks_secret = ("key-2026" in value or "password" in value.lower()
                        or value == "admin123")
        if looks_secret and value not in _ALLOWED_DEFAULTS:
            suspects.append(value)
    return suspects


def _template_keys(tpl_path, cfg_dir, prefix):
    """返回模板里出现、但对应 config.py 中不存在的键名列表（排除注释掉的行）。"""
    cfg_text = open(os.path.join(ROOT, cfg_dir, "config.py"), encoding="utf-8").read()
    keys = set()
    for line in open(tpl_path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and line.split("=", 1)[0].strip().startswith(prefix):
            keys.add(line.split("=", 1)[0].strip())
    return [k for k in sorted(keys) if ('"%s"' % k) not in cfg_text and ("'%s'" % k) not in cfg_text]


def _read_executor_version():
    path = os.path.join(ROOT, "executor", "config.py")
    for line in open(path, encoding="utf-8"):
        if line.strip().startswith("VERSION"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _zip_read(zip_path, suffix):
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if n.endswith(suffix):
                return z.read(n).decode("utf-8", "ignore")
    return ""


if __name__ == "__main__":
    main()
