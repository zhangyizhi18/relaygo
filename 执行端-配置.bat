@echo off
setlocal EnableExtensions
title 聚宽中转下单 - 执行端 配置
cd /d "%~dp0"
chcp 936 >nul

:menu
cls
echo ============================================
echo   聚宽中转下单 · 执行端  配置工具
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
echo   执行端 配置向导
echo ============================================
echo   直接回车 = 使用括号里的默认值
echo.
set "URL="
set /p "URL=  1/5 中转服务地址 [回车=http://127.0.0.1:5010]: "
if not defined URL set "URL=http://127.0.0.1:5010"
set "KEY="
set /p "KEY=  2/5 执行端密钥，要和中转服务一致 [回车=稍后再填]: "
set "MAINPATH="
set /p "MAINPATH=  3/5 同花顺行情主程序路径(hexin.exe) [回车=用默认]: "
if not defined MAINPATH set "MAINPATH=D:\同花顺软件\同花顺\hexin.exe"
set "THSPATH="
set /p "THSPATH=  4/5 同花顺下单程序路径(xiadan.exe) [回车=用默认]: "
if not defined THSPATH set "THSPATH=D:\同花顺软件\同花顺\xiadan.exe"
set "M="
set /p "M=  5/5 模式 1=模拟不下单 2=同花顺真实下单 [回车=1]: "
set "MODE=dry_run"
if "%M%"=="2" set "MODE=ths"

> ".env" echo # 执行端配置 - 由「执行端-配置.bat」于 %DATE% %TIME% 创建
>> ".env" echo # 用记事本改这个文件，改完重新启动执行端即生效
>> ".env" echo # 行首带 # 的行不生效；想让某项生效，把行首的 # 去掉
>> ".env" echo.
>> ".env" echo EXECUTOR_RELAY_URL=%URL%
if not defined KEY goto no_key
>> ".env" echo EXECUTOR_API_KEY=%KEY%
:no_key
>> ".env" echo EXECUTOR_THS_MAIN_PATH=%MAINPATH%
>> ".env" echo EXECUTOR_THS_XIADAN_PATH=%THSPATH%
>> ".env" echo EXECUTOR_MODE=%MODE%
echo.
echo   已写入配置文件: %cd%\.env
echo   提示: 执行端下次启动时，会自动把全部配置项的说明补在文件末尾。
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
for %%f in (relaygo-executor-*.exe) do if exist "%%~ff" set "PROG=%%f"
if defined PROG (
    "%PROG%" %1
    exit /b 0
)
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" executor\main.py %1
    exit /b 0
)
echo   [未找到执行端程序]
echo   请把本文件与执行端放在同一目录，或从项目根目录运行。
exit /b 1
