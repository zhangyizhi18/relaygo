@echo off
setlocal EnableExtensions
title 聚宽中转下单 - 执行端
cd /d "%~dp0"
chcp 936 >nul

echo ============================================
echo   聚宽中转下单 · 执行端
echo ============================================

rem ---- 1. 查找 Python ----
set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE (
    echo [错误] 没有找到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH
    pause & exit /b 1
)

rem ---- 2. 创建虚拟环境（仅首次） ----
if not exist "venv\Scripts\python.exe" (
    echo [1/4] 首次运行：创建虚拟环境 ...
    %PYEXE% -m venv venv
    if errorlevel 1 ( echo [错误] 创建 venv 失败 & pause & exit /b 1 )
)

set "PY=venv\Scripts\python.exe"

rem ---- 3. 安装依赖（仅首次，较慢） ----
if not exist "venv\.deps_full" (
    echo [2/4] 安装依赖 [easytrader ddddocr 等]，需几分钟 ...
    "%PY%" -m pip install -q -r requirements.txt
    if errorlevel 1 ( echo [错误] 依赖安装失败 & pause & exit /b 1 )
    rem easytrader 锁定 pywinauto 0.6.6（64位Python 批量 SendInput 有 bug），必须单独覆盖为 0.6.8
    echo [3/4] 升级 pywinauto 到 0.6.8（同花顺自动化必需）...
    "%PY%" -m pip install -q pywinauto==0.6.8
    if errorlevel 1 ( echo [错误] pywinauto 升级失败 & pause & exit /b 1 )
    type nul > "venv\.deps_full"
)

rem ---- 4. 选择执行模式 ----
echo.
echo  请选择执行模式:
echo    1. miniqmt
echo    2. ths     同花顺客户端真实下单
echo.
set "CHOICE=1"
set /p "CHOICE=  输入 1 或 2 [回车=1]: "
set "EXECUTOR_MODE=miniqmt"
if "%CHOICE%"=="2" set "EXECUTOR_MODE=ths"
echo.

echo [4/4] 启动执行端（模式: %EXECUTOR_MODE%）...
start "JQ-EXECUTOR-%EXECUTOR_MODE%" cmd /k "set EXECUTOR_MODE=%EXECUTOR_MODE%&& "%PY%" executor\main.py"
timeout /t 3 >nul
echo.
echo 已在新窗口启动，日志: executor\executor.log
echo 本窗口可以关闭。
timeout /t 5 >nul
