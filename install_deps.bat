@echo off
setlocal

rem ============================================
rem  3dsmax-mcp Python dependencies installer
rem  Run once to set up the uv environment.
rem  After this succeeds, use start_python_server.bat
rem  to launch the server.
rem ============================================

set "PROJECT_DIR=%~dp0"
rem strip trailing backslash (uv --directory rejects it)
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
cd /d "%PROJECT_DIR%"
title 3dsmax-mcp - Install Dependencies

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv not found. Install from https://docs.astral.sh/uv/
    pause
    exit /b 1
)

echo Syncing dependencies (uv sync) ...
uv sync
if errorlevel 1 (
    echo [ERROR] uv sync failed. Check network / pyproject.toml.
    pause
    exit /b 1
)

echo.
echo [OK] Dependencies installed. You can now run start_python_server.bat
pause
