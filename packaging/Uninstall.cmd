@echo off
setlocal
cd /d "%~dp0"
if /i not "%~1"=="/quiet" (
  title 卸载 Desktop Agent
  echo.
  echo   即将卸载 Desktop Agent。你的配置和工作区不会被删除。
  echo.
  choice /c YN /m "  确认卸载"
  if errorlevel 2 exit /b 0
)
set "TARGET=%~dp0"
if "%TARGET:~-1%"=="\" set "TARGET=%TARGET:~0,-1%"
cscript //nologo "%~dp0Uninstaller.vbs" "%TARGET%"
if /i not "%~1"=="/quiet" (
  echo   程序已卸载。配置和工作区已保留。
  echo   配置：%APPDATA%\DesktopAgent
  echo   工作区：%USERPROFILE%\Documents\DesktopAgent
  timeout /t 3 /nobreak >nul
)
exit /b 0
