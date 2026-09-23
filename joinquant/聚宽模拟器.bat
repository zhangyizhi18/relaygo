@echo off
chcp 936 >nul
cd /d %~dp0
rem ============================================
rem  RelayGo 聚宽模拟器 启动脚本（双击运行）
rem  带启动日志: 同目录 jq_sim_启动日志.txt
rem ============================================
set "LOG=%~dp0jq_sim_启动日志.txt"
echo.>> "%LOG%"
echo [%date% %time%] ===== 启动模拟器 =====>> "%LOG%"

set "PYEXE="
python -c "import tkinter" >> "%LOG%" 2>&1 && set "PYEXE=python"
if not defined PYEXE (
    py -3 -c "import tkinter" >> "%LOG%" 2>&1 && set "PYEXE=py -3"
)
if not defined PYEXE (
    for %%P in ("%LocalAppData%\Programs\Python\Python39\python.exe" "%ProgramFiles%\Python39\python.exe" "%LocalAppData%\Programs\Python\Python310\python.exe" "%LocalAppData%\Programs\Python\Python311\python.exe" "%LocalAppData%\Programs\Python\Python312\python.exe" "%LocalAppData%\Programs\Python\Python313\python.exe") do (
        if not defined PYEXE if exist "%%~P" (
            "%%~P" -c "import tkinter" >> "%LOG%" 2>&1 && set "PYEXE=%%~P"
        )
    )
)
if not defined PYEXE (
    echo [%date% %time%] [错误] 未找到带 tkinter 的 Python>> "%LOG%"
    echo.
    echo  [错误] 未找到带 tkinter 的 Python。
    echo  请安装官网 Python 并勾选 tcl/tk 组件后重试。
    echo  探测详情: %LOG%
    echo.
    pause
    exit /b 1
)
echo 使用解释器: %PYEXE%>> "%LOG%"

rem 前台运行：模拟器窗口出现期间本黑窗保留；关闭模拟器后本窗口自动结束
%PYEXE% jq_sim_gui.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] [异常退出] 退出码 %errorlevel%>> "%LOG%"
    echo.
    echo  [启动失败] 模拟器异常退出，退出码 %errorlevel%
    echo  详细原因: %LOG%
    echo.
    pause
) else (
    echo [%date% %time%] 模拟器正常退出>> "%LOG%"
)
exit /b 0
