@echo off
:: ============================================
:: 断开远程桌面但保持远程电脑不锁屏
:: 原理：将当前 RDP 会话重定向到物理控制台
:: ============================================

:: 获取当前会话 ID（query user 中带 "Active" 的那一行）
for /f "tokens=2,3" %%a in ('query user %USERNAME% 2^>nul') do (
    set "SESSION_ID=%%a"
)

:: 去掉 ID 前面的空格
set "SESSION_ID=%SESSION_ID: =%"

if "%SESSION_ID%"=="" (
    echo [错误] 未能获取当前会话 ID，请确认你正在远程桌面会话中运行此脚本。
    exit /b 1
)

echo 当前会话 ID 为：%SESSION_ID%
echo 正在将会话重定向到物理控制台...
echo.

:: 执行 tscon，将当前会话切换到 console
tscon %SESSION_ID% /dest:console

:: 如果执行成功，窗口会立即关闭，不会执行到下面
echo [错误] tscon 执行失败，请以管理员身份运行此脚本。
exit