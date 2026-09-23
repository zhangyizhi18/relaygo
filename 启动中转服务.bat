@echo off
setlocal EnableExtensions
title 聚宽中转下单 - 中转服务
cd /d "%~dp0"
chcp 936 >nul

echo ============================================
echo   聚宽中转下单 · 中转服务 (Flask, 端口 5010)
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
    echo [1/3] 首次运行：创建虚拟环境 ...
    %PYEXE% -m venv venv
    if errorlevel 1 ( echo [错误] 创建 venv 失败 & pause & exit /b 1 )
)

set "PY=venv\Scripts\python.exe"

rem ---- 3. 安装依赖（仅首次） ----
if not exist "venv\.deps_relay" (
    echo [2/3] 安装依赖 [flask requests] ...
    "%PY%" -m pip install -q flask requests
    if errorlevel 1 ( echo [错误] 依赖安装失败，请检查网络 & pause & exit /b 1 )
    type nul > "venv\.deps_relay"
)

echo [3/3] 启动中转服务 ...
start "JQ-RELAY-5010" cmd /k ""%PY%" relay_server\app.py"
timeout /t 2 >nul
echo.
echo 已在新窗口启动。
echo   接口状态: http://127.0.0.1:5010/api/status
echo   Web控制台: http://127.0.0.1:5010/console/   (默认 admin / admin123，登录后请改密码)
echo 本窗口可以关闭。
timeout /t 5 >nul
