@echo off
setlocal

rem ============================================
rem  3dsmax-mcp Python server launcher
rem  Requires: run install_deps.bat once first.
rem  Double-click to start. Keep this window open
rem  while the server is running.
rem ============================================

set "PROJECT_DIR=%~dp0"
rem strip trailing backslash (uv --directory rejects it)
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
cd /d "%PROJECT_DIR%"
title 3dsmax-mcp Python Server

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv not found. Run install_deps.bat first.
    pause
    exit /b 1
)

rem Launch the MCP server over HTTP bound to 0.0.0.0 so MCP clients on the
rem LAN can connect. Point your client at http://<this-ip>:8000/mcp
rem (streamable-http). Override the port via MCP_HTTP_PORT if needed.
set "MCP_TRANSPORT=streamable-http"
set "MCP_HTTP_HOST=0.0.0.0"
set "MCP_HTTP_PORT=8000"

rem Show a reachable LAN IP in the hint below instead of 0.0.0.0.
set "LOCAL_IP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object -First 1 -ExpandProperty IPAddress)"`) do set "LOCAL_IP=%%i"
if "%LOCAL_IP%"=="" set "LOCAL_IP=0.0.0.0"

echo Starting MCP server (HTTP, bound to %MCP_HTTP_HOST%:%MCP_HTTP_PORT%).
echo Connect MCP clients to:  http://%LOCAL_IP%:%MCP_HTTP_PORT%/mcp
echo Keep this window open while the server runs.
echo.

uv run --directory "%PROJECT_DIR%" 3dsmax-mcp

echo.
echo [INFO] Server exited.
pause
