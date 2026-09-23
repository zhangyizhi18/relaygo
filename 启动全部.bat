@echo off
setlocal EnableExtensions
title 聚宽中转下单 - 一键启动
cd /d "%~dp0"
chcp 936 >nul

echo ============================================
echo   聚宽中转下单 · 一键启动
echo   （中转服务 + 执行端）
echo ============================================
echo.

call "%~dp0启动中转服务.bat"
call "%~dp0启动执行端.bat"

echo.
echo 全部启动完毕：
echo   - 中转服务窗口: JQ-RELAY-5010
echo   - 执行端窗口:   JQ-EXECUTOR-xxx
echo   - 接口状态: http://127.0.0.1:5010/api/status
echo   - Web控制台: http://127.0.0.1:5010/console/   (默认 admin / admin123)
timeout /t 8 >nul
