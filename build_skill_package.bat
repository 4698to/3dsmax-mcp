@echo off
setlocal EnableDelayedExpansion

rem ============================================
rem  Build 3ds Max MCP agent skill package(s)
rem
rem  Profiles:
rem    remote  - 3dsmax-mcp-remote (default; for agent host A; HTTP scripts)
rem    local   - 3dsmax-mcp-dev (maintainer docs; no maxmcp Python in zip)
rem    both    - build both
rem
rem  Examples:
rem    build_skill_package.bat
rem    build_skill_package.bat remote
rem    build_skill_package.bat local --target none
rem    build_skill_package.bat both
rem    build_skill_package.bat --profile both --target local
rem
rem  Give A servers the remote package:
rem    dist\3dsmax-mcp-remote\  or  3dsmax-mcp-remote.skill
rem ============================================

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
cd /d "%PROJECT_DIR%"
title 3dsmax-mcp - Build Skill Package

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv not found. Install from https://docs.astral.sh/uv/
    pause
    exit /b 1
)

set "PY_ARGS="
set "SHOW_PROFILE=remote"

if "%~1"=="" (
    set "PY_ARGS=--profile remote"
    goto :run
)

if /I "%~1"=="remote" (
    set "SHOW_PROFILE=remote"
    set "PY_ARGS=--profile remote"
    shift
    goto :append_rest
)
if /I "%~1"=="local" (
    set "SHOW_PROFILE=local"
    set "PY_ARGS=--profile local"
    shift
    goto :append_rest
)
if /I "%~1"=="both" (
    set "SHOW_PROFILE=both"
    set "PY_ARGS=--profile both"
    shift
    goto :append_rest
)

rem Pass-through mode: user supplied --profile / --target themselves
set "PY_ARGS=%*"
echo %*| findstr /I /C:"--profile" >nul
if errorlevel 1 set "PY_ARGS=--profile remote %*"
echo %*| findstr /I /C:"--profile local" >nul
if not errorlevel 1 set "SHOW_PROFILE=local"
echo %*| findstr /I /C:"--profile both" >nul
if not errorlevel 1 set "SHOW_PROFILE=both"
echo %*| findstr /I /C:"--profile remote" >nul
if not errorlevel 1 set "SHOW_PROFILE=remote"
goto :run

:append_rest
if "%~1"=="" goto :run
set "PY_ARGS=!PY_ARGS! %~1"
shift
goto :append_rest

:run
echo Building skill package^(s^) ...
echo   uv run python scripts/build_skill.py %PY_ARGS%
uv run python scripts/build_skill.py %PY_ARGS%
if errorlevel 1 (
    echo [ERROR] build_skill.py failed.
    pause
    exit /b 1
)

echo.
echo [OK] Skill package^(s^) ready:
if /I "%SHOW_PROFILE%"=="local" goto :echo_local
if /I "%SHOW_PROFILE%"=="both" goto :echo_both
echo      %PROJECT_DIR%\dist\3dsmax-mcp-remote\
echo      %PROJECT_DIR%\3dsmax-mcp-remote.skill
echo.
echo For agent host A, use 3dsmax-mcp-remote ^(not the local/dev package^).
goto :done

:echo_local
echo      %PROJECT_DIR%\dist\3dsmax-mcp-dev\
echo      %PROJECT_DIR%\3dsmax-mcp-dev.skill
goto :done

:echo_both
echo      %PROJECT_DIR%\dist\3dsmax-mcp-remote\
echo      %PROJECT_DIR%\3dsmax-mcp-remote.skill
echo      %PROJECT_DIR%\dist\3dsmax-mcp-dev\
echo      %PROJECT_DIR%\3dsmax-mcp-dev.skill
echo.
echo For agent host A, use 3dsmax-mcp-remote ^(not the local/dev package^).
goto :done

:done
pause
