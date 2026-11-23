@echo off
title Kituwa Cluster - join this PC
echo.
echo   Kituwa Cluster
echo   This window joins this PC to the cluster. Nothing to type.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r = Invoke-RestMethod -Method Post -Uri 'https://k3s.kituwa.com/api/v1/enroll/auto' -ContentType 'application/json' -Body ("{""node"":""" + $env:COMPUTERNAME + """}");" ^
  "if (-not $r.ok) { Write-Host 'Could not get a join code:' $r.error -ForegroundColor Red; exit 1 };" ^
  "$env:KITUWA_JOIN_CODE = $r.code; irm https://k3s.kituwa.com/install.ps1 | iex"
echo.
echo Finished. You can close this window; the worker runs as a service.
pause
