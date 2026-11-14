@echo off
cd /d "%~dp0"
title Unfollow by Lykoon
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 unfollow_app.py %*
  goto :end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python unfollow_app.py %*
  goto :end
)
echo.
echo  Python is not installed yet.
echo  1) Install it from https://www.python.org/downloads/  (tick "Add python.exe to PATH")
echo  2) Double-click this file again.
start https://www.python.org/downloads/
:end
echo.
pause
