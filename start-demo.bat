@echo off
chcp 65001 >nul
title Invoice Precheck - Local Demo (方案A: Cloudflare Tunnel)
echo ============================================================
echo   发票合规预审 - 本地演示一键启动（方案 A：本地穿透）
echo   后端 FastAPI + Cloudflare Tunnel 公网地址
echo ============================================================
echo.

REM 1) 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python。请先安装 Python 3.11+（勾选 "Add Python to PATH"）。
    pause
    exit /b 1
)

REM 2) 检查 cloudflared
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [提示] 未检测到 cloudflared。请先安装：
    echo   1. 打开 https://github.com/cloudflare/cloudflared/releases
    echo   2. 下载 cloudflared-windows-amd64.exe
    echo   3. 重命名为 cloudflared.exe 并放到本目录（invoice-precheck\）
    pause
    exit /b 1
)

REM 3) 安装依赖
echo [1/3] 安装 Python 依赖...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

REM 4) API Key（未设置则自动生成一个随机的；正式公网部署请用强 key）
if "%INVOICE_API_KEY%"=="" (
    set "INVOICE_API_KEY=inv-%RANDOM%%RANDOM%%RANDOM%"
)
echo.
echo [2/3] 本机 API Key（业务接口需请求头 X-API-Key 携带）:
echo        %INVOICE_API_KEY%
echo.

REM 5) 启动后端（新窗口，保持运行）
echo [3/3] 启动后端 FastAPI (http://localhost:8000)...
start "invoice-precheck-backend" cmd /k "set INVOICE_API_KEY=%INVOICE_API_KEY% && uvicorn app.main:app --host 0.0.0.0 --port 8000"

REM 6) 等后端就绪，再开公网隧道
timeout /t 4 /nobreak >nul
echo.
echo 后端已启动。正在建立公网隧道，请复制下面输出中的 https://xxx.trycloudflare.com 地址：
echo （该地址就是你的公网演示入口；关闭本窗口即停止隧道）
echo.
cloudflared tunnel --url http://localhost:8000

pause
