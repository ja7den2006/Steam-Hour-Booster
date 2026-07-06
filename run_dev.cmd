@echo off
setlocal

set "REPO_ROOT=%~dp0"
cd /d "%REPO_ROOT%"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.8 or newer is required on PATH.
  exit /b 1
)

set "SKIP_INSTALL="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="-SkipInstall" set "SKIP_INSTALL=1"
shift
goto parse_args

:args_done
if not defined SKIP_INSTALL (
  echo Installing editable desktop dependencies...
  python -m pip install -e .[dev]
  if errorlevel 1 exit /b %errorlevel%
)

echo Launching Steam Hour Booster...
python -m steam_hour_booster
set "EXIT_CODE=%errorlevel%"
endlocal & exit /b %EXIT_CODE%
