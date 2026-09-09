@echo off
setlocal

rem ============================================
rem  3dsmax-mcp Python server launcher (stdio)
rem  Requires: run install_deps.bat once first.
rem  Double-click to start. Keep this window open
rem  while the server is running.
rem ============================================

set "PROJECT_DIR=%~dp0"
rem strip trailing backslash (uv --directory rejects it)
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
cd /d "%PROJECT_DIR%"
title 3dsmax-mcp Python Server (stdio)

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv not found. Run install_deps.bat first.
    pause
    exit /b 1
)

rem stdio transport: MCP clients (Claude Desktop, Cursor, ...) normally launch
rem this server themselves. This file runs it standalone / for testing.
set "MCP_TRANSPORT=stdio"

echo Starting MCP server (stdio transport).
echo Same mode as MCP client command:  3dsmax-mcp   --or--   uv run 3dsmax-mcp
echo Keep this window open while the server runs.
echo.

uv run --directory "%PROJECT_DIR%" 3dsmax-mcp

echo.
echo [INFO] Server exited.
pause
