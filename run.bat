@echo off
setlocal enabledelayedexpansion

set VENV=.venv
set PYTHON=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe

if "%~1"=="" goto help
if "%~1"=="help" goto help
if "%~1"=="install" goto install
if "%~1"=="report-2w" goto report-2w
if "%~1"=="report-monthly" goto report-monthly
if "%~1"=="report-last-month" goto report-last-month
if "%~1"=="collect-2w" goto collect-2w
if "%~1"=="collect-monthly" goto collect-monthly
if "%~1"=="collect-last-month" goto collect-last-month
if "%~1"=="analyze-2w" goto analyze-2w
if "%~1"=="analyze-monthly" goto analyze-monthly
if "%~1"=="analyze-last-month" goto analyze-last-month
if "%~1"=="clean" goto clean
if "%~1"=="uninstall" goto uninstall
echo Unknown command: %~1
goto help

:help
echo.
echo   Self-Reporting — Performance Self-Assessment
echo   ---------------------------------------------
echo   run install              Create venv and install dependencies
echo.
echo   Full report (GitHub + Jira + Claude AI analysis):
echo   run report-2w            Last 2 weeks
echo   run report-monthly       Current month
echo   run report-last-month    Last complete month
echo.
echo   Data collection only:
echo   run collect-2w
echo   run collect-monthly
echo   run collect-last-month
echo.
echo   Re-run AI analysis:
echo   run analyze-2w
echo   run analyze-monthly
echo   run analyze-last-month
echo.
echo   Language override:
echo   run report-2w es
echo   run report-monthly en
echo.
goto :eof

:install
if not exist %VENV% python -m venv %VENV%
%PIP% install --upgrade pip
%PIP% install -r requirements.txt
goto :eof

:report-2w
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py report --period biweekly %LANGFLAG%
goto :eof

:report-monthly
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py report --period monthly %LANGFLAG%
goto :eof

:report-last-month
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py report --period last-month %LANGFLAG%
goto :eof

:collect-2w
%PYTHON% main.py collect --period biweekly
goto :eof

:collect-monthly
%PYTHON% main.py collect --period monthly
goto :eof

:collect-last-month
%PYTHON% main.py collect --period last-month
goto :eof

:analyze-2w
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py analyze --period biweekly %LANGFLAG%
goto :eof

:analyze-monthly
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py analyze --period monthly %LANGFLAG%
goto :eof

:analyze-last-month
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py analyze --period last-month %LANGFLAG%
goto :eof

:clean
if exist output rmdir /s /q output
if exist logs rmdir /s /q logs
goto :eof

:uninstall
if exist %VENV% rmdir /s /q %VENV%
goto :eof
