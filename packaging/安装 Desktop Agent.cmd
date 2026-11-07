@echo off
setlocal EnableExtensions
title Desktop Agent 安装程序

set "SRC=%~dp0"
if exist "%~dp0payload\app\main.py" set "SRC=%~dp0payload"

set "TARGET=%LOCALAPPDATA%\Programs\DesktopAgent"
if defined DESKTOP_AGENT_INSTALL_DIR set "TARGET=%DESKTOP_AGENT_INSTALL_DIR%"

echo.
echo   Desktop Agent 1.2.0
echo   安装到：%TARGET%
echo   不需要管理员权限，不需要预装 Python。
echo.

if not exist "%SRC%\app\main.py" (
  echo [错误] 安装包不完整：找不到 app\main.py。
  pause
  exit /b 1
)
if not exist "%SRC%\runtime\python\pythonw.exe" (
  echo [错误] 安装包不完整：找不到 runtime\python\pythonw.exe。
  pause
  exit /b 1
)

set "FIRST=0"
if not exist "%APPDATA%\DesktopAgent\.env" set "FIRST=1"

echo [1/4] 复制程序、源码和运行时...
if exist "%TARGET%" rmdir /s /q "%TARGET%" >nul 2>&1
mkdir "%TARGET%" >nul 2>&1
xcopy "%SRC%\*" "%TARGET%\" /E /I /H /Y /Q >nul
if errorlevel 1 (
  echo [错误] 复制失败，请关闭正在运行的 Desktop Agent 后重试。
  pause
  exit /b 1
)

echo [2/4] 创建配置和用户工作区...
if not exist "%APPDATA%\DesktopAgent" mkdir "%APPDATA%\DesktopAgent" >nul 2>&1
if not exist "%APPDATA%\DesktopAgent\.session" mkdir "%APPDATA%\DesktopAgent\.session" >nul 2>&1
if not exist "%APPDATA%\DesktopAgent\.env" copy /y "%TARGET%\app\.env.example" "%APPDATA%\DesktopAgent\.env" >nul
if not exist "%USERPROFILE%\Documents\DesktopAgent\workspace" mkdir "%USERPROFILE%\Documents\DesktopAgent\workspace" >nul 2>&1

echo [3/4] 检查 WebView2...
set "WEBVIEW=0"
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>&1 && set "WEBVIEW=1"
reg query "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>&1 && set "WEBVIEW=1"
reg query "HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>&1 && set "WEBVIEW=1"
if "%WEBVIEW%"=="0" (
  echo   未检测到 WebView2。程序窗口需要它，正在打开微软下载页...
  start "" "https://developer.microsoft.com/microsoft-edge/webview2/"
)

echo [4/4] 创建快捷方式和卸载入口...
if not defined DESKTOP_AGENT_SKIP_SHORTCUTS cscript //nologo "%TARGET%\Shortcuts.vbs" "%TARGET%" "%APPDATA%\DesktopAgent\.env" "%USERPROFILE%\Documents\DesktopAgent\workspace"

echo.
echo   安装完成！
echo   程序：%TARGET%
echo   配置：%APPDATA%\DesktopAgent\.env
echo   工作区：%USERPROFILE%\Documents\DesktopAgent\workspace
echo.

if not defined DESKTOP_AGENT_NO_LAUNCH start "" wscript.exe "%TARGET%\启动 Desktop Agent.vbs"
if "%FIRST%"=="1" if not defined DESKTOP_AGENT_NO_LAUNCH (
  echo   首次安装已打开配置文件，请把 LLM_API_KEY 换成自己的 Key 后保存。
  start "" notepad.exe "%APPDATA%\DesktopAgent\.env"
)
timeout /t 4 /nobreak >nul
exit /b 0
