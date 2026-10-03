@echo off
setlocal
cd /d "%~dp0"
title knowledge_growth

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 scripts\bootstrap_dev.py --launch
  goto :end
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python scripts\bootstrap_dev.py --launch
  goto :end
)

echo.
echo Python 3.11 or newer was not found.
echo Install Python and make sure "py" or "python" is available on PATH.
echo.
pause

:end
endlocal
