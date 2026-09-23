# -*- coding: utf-8 -*-
"""
中转服务 exe 打包脚本（可反复使用）。

用法:
    python release/build_exe.py
产物:
    release/<版本号>-<时间戳>/jq-relay-<版本号>-<时间戳>-win64.exe
    release/<版本号>-<时间戳>/md5.txt        （追加一行本文件的 md5）

流程: 找到带 flask 的 Python -> PyInstaller 打包 -> 真跑 exe 自检
      （/api/status 版本号 + /console/ 控制台页面）-> 输出 md5。

时间戳规则见 release/release_common.py；build_all.py 会用同一时间戳串起三个包。
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_common as rc  # noqa: E402

ROOT = rc.ROOT
TS = rc.release_ts()
OUT = rc.out_dir(TS)
NAME = rc.package_name("relaygo-relay", TS, ext="")   # jq-relay-<版本>-<时间>-win64
EXE = os.path.join(OUT, NAME + ".exe")
TEST_PORT = 5099                          # 自检端口，避开正在跑的 5010


def main():
    py = rc.pick_python(("flask",))
    print("[1/3] 使用解释器: %s" % py)
    print("      版本=%s 时间戳=%s" % (rc.VER, TS))

    # ---- PyInstaller 打包（改变任何 .py 都要重跑，否则包里是旧逻辑）----
    print("[2/3] PyInstaller 打包 ...")
    # 正式版锁标记：打进 exe 后 RELAY_LICENSE_ENABLED=0 一律强制无效
    #（防拿到包的用户改 .env 绕过授权），见 relay_server/config.py。
    workdir = os.path.join(rc.BUILD_DIR, "relay_%s" % TS)
    lock_file = os.path.join(workdir, "release.lock")
    os.makedirs(workdir, exist_ok=True)
    with open(lock_file, "w", encoding="utf-8") as f:
        f.write("RelayGo release build lock. 授权开关已锁定，RELAY_LICENSE_ENABLED=0 无效。\n")
    cmd = [py, "-m", "PyInstaller",
           "--onefile", "--console",
           "--name", NAME,
           "--distpath", OUT,
           "--workpath", workdir,
           "--specpath", workdir,
           "--add-data", "%s%s." % (lock_file, os.pathsep),
           "--paths", os.path.join(ROOT, "relay_server"),
           os.path.join(ROOT, "relay_server", "app.py")]
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        sys.exit("[错误] PyInstaller 打包失败")
    assert os.path.exists(EXE), "exe 未生成: %s" % EXE

    # ---- 自检：真跑 exe，验证版本号与控制台页面 ----
    print("[3/3] 自检：启动 exe 验证 /api/status 与 /console/ ...")
    check_db = os.path.join(tempfile.gettempdir(),
                            "jq_relay_selfcheck_%d.db" % int(time.time() * 1000))
    # JQ_ENVFILE_NOCREATE=1：自检时禁止 exe 在发布目录自动生成 .env，保证构建结果确定
    env = dict(os.environ, RELAY_PORT=str(TEST_PORT), RELAY_DB_PATH=check_db,
               JQ_ENVFILE_NOCREATE="1")
    proc = subprocess.Popen([EXE], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
    # 绕开系统代理：HTTP_PROXY 会把 127.0.0.1 的请求也劫持走
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        # onefile 首次启动要解压自身，可能十几秒，给足 30 秒
        for _ in range(60):
            time.sleep(0.5)
            try:
                body = opener.open(
                    "http://127.0.0.1:%d/api/status" % TEST_PORT,
                    timeout=2).read().decode()
                if rc.VER in body:
                    print("      自检通过: /api/status -> %s" % body)
                    ok = True
                    break
            except Exception:
                continue
        if ok:
            # 正式版锁下授权强制启用：未激活时 /console/ 会 302 到激活页（属预期），
            # 已激活/授权服务器不可达宽限等场景返回控制台本体。两种都算通过。
            page = opener.open(
                "http://127.0.0.1:%d/console/" % TEST_PORT,
                timeout=3).read().decode("utf-8", "ignore")
            assert "RelayGo" in page and ("控制台" in page or "授权激活" in page), \
                "exe 内页面异常（既不是控制台也不是激活页）"
            state = "控制台" if "控制台" in page else "激活页(未激活,锁定生效)"
            print("      自检通过: /console/ -> %s，%d 字节" % (state, len(page)))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not ok:
        sys.exit("[错误] 自检失败: exe 启动后 /api/status 未返回版本 %s" % rc.VER)

    digest = rc.md5(EXE)
    print("\n打包完成: %s\nmd5: %s" % (EXE, digest))
    _write_md5(OUT, NAME + ".exe", digest)


def _write_md5(out, filename, digest):
    path = os.path.join(out, "md5.txt")
    with open(path, "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (digest, filename))


if __name__ == "__main__":
    main()
