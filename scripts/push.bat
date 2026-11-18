@echo off
chcp 65001 >nul
setlocal
title Push astrbot_plugin_daily_news to GitHub
cd /d "%~dp0"

echo ==========================================
echo  AstrBot daily-news plugin - push to GitHub
echo ==========================================
echo.
echo Create an EMPTY repo on GitHub first (no README / .gitignore / license).
echo Example: https://github.com/YOUR_NAME/astrbot_plugin_daily_news
echo.

set "REPO_URL="
set /p REPO_URL=Paste repo URL (https://github.com/NAME/astrbot_plugin_daily_news.git):
if "%REPO_URL%"=="" (
  echo No repo URL, exit.
  pause
  exit /b 1
)

set "PROXY="
set /p PROXY=Local proxy URL (leave empty if none, e.g. http://127.0.0.1:7890):

set "INSECURE="
set /p INSECURE=If git reports a certificate/revocation error, type y (default n):

set "EXTRA="
if not "%PROXY%"=="" set "EXTRA=-Proxy %PROXY%"
if /i "%INSECURE%"=="y" set "EXTRA=%EXTRA% -InsecureTLS"

echo.
echo Running...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0init-and-push.ps1" -RepoUrl "%REPO_URL%" %EXTRA% -Push

echo.
pause
