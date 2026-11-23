@echo off
title Kituwa Cluster - join this PC
echo.
echo   Kituwa Cluster
echo   This window installs the worker and joins it to the cluster.
echo.
set /p CODE=Paste your join code (starts with k3s1.) and press Enter: 
if "%CODE%"=="" (
  echo No code entered. Get one at https://k3s.kituwa.com/join/
  pause
  exit /b 1
)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:KITUWA_JOIN_CODE='%CODE%'; irm https://k3s.kituwa.com/install.ps1 | iex"
echo.
echo Finished. You can close this window; the worker runs as a service.
pause
