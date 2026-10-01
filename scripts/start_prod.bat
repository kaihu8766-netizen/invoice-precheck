@echo off
rem invoice-precheck 真实模式一键启动（Windows 双击即用）
rem 等价命令：py -3 scripts\start_prod.py

cd /d "%~dp0.."

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 scripts\start_prod.py %*
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python scripts\start_prod.py %*
    goto :eof
)

echo [invoice-precheck] 未找到 Python。请先安装 Python 3.10+：
echo   https://www.python.org/downloads/
echo 安装时勾选 "Add Python to PATH"，然后重新双击本脚本。
pause
