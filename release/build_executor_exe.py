# -*- coding: utf-8 -*-
"""
执行端 exe 打包脚本（可反复使用）。

用法:
    python release/build_executor_exe.py
产物:
    release/<版本号>-<时间戳>/jq-executor-<版本号>-<时间戳>-win64.exe

说明:
  * 执行端依赖 easytrader / ddddocr / pywinauto / pywin32，这些包带模型与配置数据，
    必须用 --collect-all 一并收进 exe，否则运行时报"缺模型 / 缺配置文件"。
  * 自动选用装了上述依赖的项目 venv 解释器打包（见 release_common.pick_python）。
  * 自检：带 EXECUTOR_SELFCHECK=1 真跑 exe，逐个 import 券商适配器与 ddddocr，
    证实重依赖确实打进来了（不做任何连接/交易）。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_common as rc  # noqa: E402

ROOT = rc.ROOT
TS = rc.release_ts()
OUT = rc.out_dir(TS)
NAME = rc.package_name("relaygo-executor", TS, ext="")   # jq-executor-<版本>-<时间>-win64
EXE = os.path.join(OUT, NAME + ".exe")

# 带数据/子模块的重依赖：--collect-all 收全（模型文件、配置模板、comtypes 运行时）
COLLECT_ALL = ["easytrader", "ddddocr", "pywinauto", "comtypes", "onnxruntime", "cv2"]
# 执行端自己的模块（在函数内 import，显式声明防止漏收）
HIDDEN = ["broker_base", "broker_ths", "broker_thsauto", "broker_miniqmt",
          "captcha", "pywinauto_compat", "foreground", "risk", "tasks",
          "win32clipboard", "win32gui", "win32api", "win32con"]
# 显式排除 xtquant：它是券商随 QMT 安装的第三方库，需与 QMT 版本匹配、体积很大
# （含 cp36~cp313 多版本 .pyd + 约 34MB 的 datacenter_shared.dll），不应固化进 exe。
# 运行时由 executor/main.py 把「exe 所在目录」加入 sys.path，从 exe 同级的 xtquant/ 加载。
# 不排除的话，只要打包机的搜索路径里存在 xtquant（例如项目根被放了副本），PyInstaller
# 就会尝试整包收集；副本不全时直接 FileNotFoundError 让发版失败（2026-09-15 踩坑）。
EXCLUDES = ["xtquant"]


def main():
    py = rc.pick_python(("easytrader", "ddddocr", "pywinauto", "win32gui"))
    print("[1/3] 使用解释器: %s" % py)
    print("      版本=%s 时间戳=%s" % (rc.VER, TS))

    print("[2/3] PyInstaller 打包（收集 easytrader/ddddocr/pywinauto 等，耗时较长）...")
    cmd = [py, "-m", "PyInstaller",
           "--onefile", "--console",
           "--name", NAME,
           "--distpath", OUT,
           "--workpath", os.path.join(rc.BUILD_DIR, "executor_%s" % TS),
           "--specpath", os.path.join(rc.BUILD_DIR, "executor_%s" % TS),
           "--paths", os.path.join(ROOT, "executor"),
           "--noconfirm"]
    for pkg in COLLECT_ALL:
        cmd += ["--collect-all", pkg]
    for mod in HIDDEN:
        cmd += ["--hidden-import", mod]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(os.path.join(ROOT, "executor", "main.py"))
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        sys.exit("[错误] PyInstaller 打包失败")
    assert os.path.exists(EXE), "exe 未生成: %s" % EXE

    print("[3/3] 自检：以 EXECUTOR_SELFCHECK=1 启动 exe，验证重依赖已打包 ...")
    # 强制子进程用 UTF-8 输出（Windows 控制台默认 GBK，中文日志会让 utf-8 解码报错）
    # JQ_ENVFILE_NOCREATE=1：自检时禁止 exe 在发布目录自动生成 .env，保证构建结果确定
    env = dict(os.environ, EXECUTOR_SELFCHECK="1", EXECUTOR_MODE="dry_run",
               PYTHONIOENCODING="utf-8", JQ_ENVFILE_NOCREATE="1")
    r = subprocess.run([EXE], env=env, cwd=OUT, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding="utf-8",
                       errors="replace", timeout=300)
    out = r.stdout or ""
    if "SELFCHECK_OK" not in out:
        sys.exit("[错误] 执行端自检失败，输出：\n%s" % out)
    line = [l for l in out.splitlines() if "SELFCHECK_OK" in l][0]
    print("      %s" % line)

    digest = rc.md5(EXE)
    print("\n打包完成: %s\nmd5: %s" % (EXE, digest))
    _write_md5(OUT, NAME + ".exe", digest)


def _write_md5(out, filename, digest):
    with open(os.path.join(out, "md5.txt"), "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (digest, filename))


if __name__ == "__main__":
    main()
