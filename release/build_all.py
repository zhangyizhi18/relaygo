# -*- coding: utf-8 -*-
"""
一键发版 —— 按固定约定产出全套发布物。

用法:
    python release/build_all.py                 # 用当前时间做时间戳
    set RELEASE_TS=20260911-1602 & python release/build_all.py   # 指定时间戳

产出（全部放在 release/<版本号>-<时间戳>/ 目录）:
    relaygo-relay-<版本>-<时间>-win64.exe       中转服务 exe
    relaygo-executor-<版本>-<时间>-win64.exe    执行端 exe
    relaygo-relay-docker-<版本>-<时间>.zip      中转服务 docker 源码包
    用户手册-<版本>-<时间>.html                 详细用户手册（由模板渲染）
    升级说明-<版本>-<时间>.txt                  本次更新说明
    md5.txt                                     三个二进制的 md5
    发版清单.txt                                 本次发版清单

约定（自 v1.3.0 起固定）: 名称-版本号-时间。版本号唯一来源 relay_server/config.py 的 VERSION。
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_common as rc  # noqa: E402

ROOT = rc.ROOT
HERE = os.path.dirname(os.path.abspath(__file__))


def run(script):
    print("\n" + "=" * 64 + "\n>>> %s\n" % script + "=" * 64)
    if subprocess.run([sys.executable, os.path.join(HERE, script)],
                      cwd=ROOT, env=os.environ).returncode != 0:
        sys.exit("[错误] %s 执行失败，发版中止" % script)


def render(template_path, out_path, mapping):
    with open(template_path, encoding="utf-8") as f:
        text = f.read()
    for k, v in mapping.items():
        text = text.replace("{{%s}}" % k, v)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


def render_env_example(py_dir, out_path, header):
    """把某一端的 env_template.TEMPLATE 渲染成 .env.example。

    写 UTF-8 带 BOM：用户用记事本打开中文说明不乱码。
    """
    d = os.path.join(ROOT, py_dir)
    spec = importlib.util.spec_from_file_location(
        "tpl_" + py_dir, os.path.join(d, "env_template.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, d)          # env_template 里 `import envfile` 需要本目录在路径上
    try:
        spec.loader.exec_module(mod)
    finally:
        if sys.path and sys.path[0] == d:
            sys.path.pop(0)
    with open(out_path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(header + mod.TEMPLATE)


def config_assets(out, ts):
    """产出「不会用命令行也能改配置」的一整套东西：配置模板 + 双击式配置 bat。

    bat 用二进制复制，确保 GBK 编码与 CRLF 换行原样保留（cmd 对这两点很敏感）。
    """
    made = []
    for py_dir, tpl_title, title in (("relay_server", "中转服务配置模板", "中转服务"),
                                     ("executor", "执行端配置模板", "执行端")):
        name = "%s-%s-%s.env.example" % (tpl_title, rc.VER, ts)
        header = ("# %s 配置模板（参考用）\n"
                  "# 用法：放到程序同目录、改名成 .env 即可生效。\n"
                  "# 其实程序第一次运行时也会自动在旁边生成一份带完整说明的 .env，\n"
                  "# 所以这份模板是「先看后配」用的，直接下载也不影响。\n"
                  "# 版本 %s    生成时间 %s\n\n" % (title, rc.VER, ts))
        render_env_example(py_dir, os.path.join(out, name), header)
        made.append(name)
    for bat in ("中转服务-配置.bat", "执行端-配置.bat"):
        src = os.path.join(ROOT, bat)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out, bat))
            made.append(bat)
    return made


def main():
    ts = os.environ.get("RELEASE_TS") or datetime.now().strftime("%Y%m%d-%H%M")
    os.environ["RELEASE_TS"] = ts            # 锁定，三个子脚本共用同一时间戳
    out = rc.out_dir(ts)
    mapping = {"VER": rc.VER, "TS": ts,
               "DATE": datetime.now().strftime("%Y-%m-%d"),
               "FOLDER": "%s-%s" % (rc.VER, ts)}

    print("发版版本: %s    时间戳: %s" % (rc.VER, ts))
    print("输出目录: %s" % out)

    # 1~3. 三个二进制/源码包（SKIP_BUILD=1 时仅收尾：适用于某个包已单独构建好、只要补齐手册与自检）
    if os.environ.get("SKIP_BUILD") == "1":
        print("[跳过] 三个包已存在，仅执行收尾（手册/升级说明/清单/自检）")
    else:
        run("build_docker.py")
        run("build_exe.py")
        run("build_executor_exe.py")

    # 4. 用户手册（模板 -> 带版本号文件名）
    manual_tpl = os.path.join(HERE, "manual_template.html")
    manual_name = "用户手册-%s-%s.html" % (rc.VER, ts)
    render(manual_tpl, os.path.join(out, manual_name), mapping)

    # 5. 升级说明（编辑 release/升级说明.txt 即可，发版时自动带版本号落盘）
    notes_src = os.path.join(HERE, "升级说明.txt")
    notes_name = "升级说明-%s-%s.txt" % (rc.VER, ts)
    if os.path.exists(notes_src):
        render(notes_src, os.path.join(out, notes_name), mapping)

    # 6. 配置模板 + 双击式配置 bat（面向不会用命令行的用户）
    cfg_items = config_assets(out, ts)

    # 7. 发版清单 + 统一重算 md5（权威且自愈：即使某个包是单独构建的，这里也会补齐）
    items = [rc.package_name("relaygo-relay", ts), rc.package_name("relaygo-executor", ts),
             rc.package_name("relaygo-relay-docker", ts, ext="zip", platform=None),
             manual_name, notes_name] + cfg_items
    with open(os.path.join(out, "md5.txt"), "w", encoding="utf-8") as f:
        for it in items:
            p = os.path.join(out, it)
            if it.endswith((".exe", ".zip")) and os.path.exists(p):
                f.write("%s  %s\n" % (rc.md5(p), it))
    with open(os.path.join(out, "发版清单.txt"), "w", encoding="utf-8") as f:
        f.write("RelayGo 发版清单\n版本: %s\n时间: %s\n目录: %s\n\n"
                % (rc.VER, ts, out))
        for it in items:
            f.write("  - %s\n" % it)

    # 8. 包自检
    run("verify_release.py")

    print("\n" + "=" * 64)
    print("发版完成 -> %s" % out)
    for it in items:
        print("   %s" % it)
    print("=" * 64)


if __name__ == "__main__":
    main()
