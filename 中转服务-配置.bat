@echo off
setlocal EnableExtensions
title 聚宽中转下单 - 中转服务 配置
cd /d "%~dp0"
chcp 936 >nul

:menu
cls
echo ============================================
echo   聚宽中转下单 · 中转服务  配置工具
echo ============================================
echo.
echo   目录: %cd%
if exist ".env" goto has_env
echo   状态: 还没有配置文件，选 [1] 会自动创建
goto show_menu
:has_env
echo   状态: 配置文件 .env 已存在
:show_menu
echo.
echo   [1] 问答式配置  - 跟着提示填空就行，不用懂命令
echo   [2] 用记事本打开配置文件
echo   [3] 查看当前生效的配置
echo   [0] 退出
echo.
set "C="
set /p "C=  请输入 1 / 2 / 3 / 0 后回车: "
if "%C%"=="1" goto wizard
if "%C%"=="2" goto edit
if "%C%"=="3" goto showcfg
if "%C%"=="0" exit /b 0
goto menu

:edit
if exist ".env" goto do_edit
echo.
echo   [提示] 还没有配置文件，请先选 [1] 创建。
echo.
pause
goto menu

:do_edit
start "" notepad ".env"
goto menu

:showcfg
cls
call :runprog --show-config
echo.
pause
goto menu

:wizard
if exist ".env" goto already
cls
echo ============================================
echo   中转服务 配置向导
echo ============================================
echo   直接回车 = 使用括号里的默认值
echo.
set "SIG="
set /p "SIG=  1/4 聚宽信号密钥，要和聚宽策略里的一致 [回车=用默认]: "
if not defined SIG set "SIG=jq-signal-key-2026-change-me"
set "EXE="
set /p "EXE=  2/4 执行端密钥，要和执行端一致 [回车=用默认]: "
if not defined EXE set "EXE=executor-key-2026-change-me"
set "PWD="
set /p "PWD=  3/4 控制台管理员密码 [回车=用默认 admin123]: "
if not defined PWD set "PWD=admin123"
set "PORT="
set /p "PORT=  4/4 监听端口 [回车=5010]: "
if not defined PORT set "PORT=5010"

> ".env" echo # 中转服务配置 - 由「中转服务-配置.bat」于 %DATE% %TIME% 创建
>> ".env" echo # 用记事本改这个文件，改完重新启动中转服务即生效
>> ".env" echo # 行首带 # 的行不生效；想让某项生效，把行首的 # 去掉
>> ".env" echo.
>> ".env" echo RELAY_SIGNAL_KEY=%SIG%
>> ".env" echo RELAY_EXECUTOR_KEY=%EXE%
>> ".env" echo RELAY_WEB_ADMIN_PASSWORD=%PWD%
>> ".env" echo RELAY_PORT=%PORT%
echo.
echo   已写入配置文件: %cd%\.env
echo   提示: 中转服务下次启动时，会自动把全部配置项的说明补在文件末尾。
echo.
call :runprog --show-config
echo.
pause
goto menu

:already
cls
echo.
echo   配置文件 .env 已经存在。
echo.
echo   为了不覆盖你已经填好的内容，向导不会改动它。你可以：
echo     1) 选 [2] 用记事本直接修改  推荐
echo     2) 或先把 .env 改名成 .env.old，再回来选 [1] 重新走一遍向导
echo.
pause
goto menu

:runprog
set "PROG="
for %%f in (relaygo-relay-*.exe) do if exist "%%~ff" set "PROG=%%f"
if defined PROG (
    "%PROG%" %1
    exit /b 0
)
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" relay_server\app.py %1
    exit /b 0
)
echo   [未找到中转服务程序]
echo   请把本文件与中转服务放在同一目录，或从项目根目录运行。
exit /b 1
