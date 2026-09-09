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

rem Resolve the LAN IP the MCP server is reachable at (fallback: 127.0.0.1).
set "LOCAL_IP=127.0.0.1"
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' } | Sort-Object InterfaceMetric | Select-Object -First 1 -ExpandProperty IPAddress)"`) do set "LOCAL_IP=%%i"

echo Starting MCP server (streamable-http, http://%LOCAL_IP%:8000/mcp)
echo Keep this window open while the server runs.
echo.

uv run --directory "%PROJECT_DIR%" 3dsmax-mcp

echo.
echo [INFO] Server exited.
pause
