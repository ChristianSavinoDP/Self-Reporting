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
if "%~1"=="report-yearly" goto report-yearly
if "%~1"=="report-historic" goto report-historic
if "%~1"=="report-custom" goto report-custom
if "%~1"=="collect-2w" goto collect-2w
if "%~1"=="collect-monthly" goto collect-monthly
if "%~1"=="collect-last-month" goto collect-last-month
if "%~1"=="collect-yearly" goto collect-yearly
if "%~1"=="collect-historic" goto collect-historic
if "%~1"=="collect-custom" goto collect-custom
if "%~1"=="analyze-2w" goto analyze-2w
if "%~1"=="analyze-monthly" goto analyze-monthly
if "%~1"=="analyze-last-month" goto analyze-last-month
if "%~1"=="analyze-yearly" goto analyze-yearly
if "%~1"=="analyze-historic" goto analyze-historic
if "%~1"=="analyze-custom" goto analyze-custom
if "%~1"=="html-2w" goto html-2w
if "%~1"=="html-monthly" goto html-monthly
if "%~1"=="html-last-month" goto html-last-month
if "%~1"=="html-yearly" goto html-yearly
if "%~1"=="html-historic" goto html-historic
if "%~1"=="html-custom" goto html-custom
if "%~1"=="clean" goto clean
if "%~1"=="uninstall" goto uninstall
echo Unknown command: %~1
goto help

:help
echo.
echo   Self-Reporting: Performance Self-Assessment
echo   ---------------------------------------------
echo   Run this from Claude Code (it authenticates the AI analysis and runs it on Opus).
echo.
echo   run install              Create venv and install dependencies
echo.
echo   Full report (GitHub + Jira + Claude AI analysis + HTML):
echo   run report-2w            Last 2 weeks
echo   run report-monthly       Current month
echo   run report-last-month    Last complete month
echo   run report-yearly        Year to date
echo   run report-historic      All time
echo   run report-custom 2026-01-01 2026-03-31
echo.
echo   Data collection only:
echo   run collect-2w ^| collect-monthly ^| collect-last-month
echo   run collect-yearly ^| collect-historic
echo   run collect-custom 2026-01-01 2026-03-31
echo.
echo   Re-run AI analysis:
echo   run analyze-2w ^| analyze-monthly ^| analyze-last-month
echo   run analyze-yearly ^| analyze-historic
echo   run analyze-custom 2026-01-01 2026-03-31
echo.
echo   Render existing report to standalone HTML:
echo   run html-2w ^| html-monthly ^| html-last-month
echo   run html-yearly ^| html-historic
echo   run html-custom 2026-01-01 2026-03-31
echo.
echo   Language override (report/analyze only): run report-2w es
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

:report-yearly
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py report --period yearly %LANGFLAG%
goto :eof

:report-historic
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py report --period historic %LANGFLAG%
goto :eof

:report-custom
if "%~2"=="" echo START date required. Usage: run report-custom 2026-01-01 2026-03-31 & goto :eof
if "%~3"=="" echo END date required. Usage: run report-custom 2026-01-01 2026-03-31 & goto :eof
set LANGFLAG=
if not "%~4"=="" set LANGFLAG=--language %~4
%PYTHON% main.py report --period custom --start-date %~2 --end-date %~3 %LANGFLAG%
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

:collect-yearly
%PYTHON% main.py collect --period yearly
goto :eof

:collect-historic
%PYTHON% main.py collect --period historic
goto :eof

:collect-custom
if "%~2"=="" echo START date required. Usage: run collect-custom 2026-01-01 2026-03-31 & goto :eof
if "%~3"=="" echo END date required. Usage: run collect-custom 2026-01-01 2026-03-31 & goto :eof
%PYTHON% main.py collect --period custom --start-date %~2 --end-date %~3
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

:analyze-yearly
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py analyze --period yearly %LANGFLAG%
goto :eof

:analyze-historic
set LANGFLAG=
if not "%~2"=="" set LANGFLAG=--language %~2
%PYTHON% main.py analyze --period historic %LANGFLAG%
goto :eof

:analyze-custom
if "%~2"=="" echo START date required. Usage: run analyze-custom 2026-01-01 2026-03-31 & goto :eof
if "%~3"=="" echo END date required. Usage: run analyze-custom 2026-01-01 2026-03-31 & goto :eof
set LANGFLAG=
if not "%~4"=="" set LANGFLAG=--language %~4
%PYTHON% main.py analyze --period custom --start-date %~2 --end-date %~3 %LANGFLAG%
goto :eof

:html-2w
%PYTHON% main.py html --period biweekly
goto :eof

:html-monthly
%PYTHON% main.py html --period monthly
goto :eof

:html-last-month
%PYTHON% main.py html --period last-month
goto :eof

:html-yearly
%PYTHON% main.py html --period yearly
goto :eof

:html-historic
%PYTHON% main.py html --period historic
goto :eof

:html-custom
if "%~2"=="" echo START date required. Usage: run html-custom 2026-01-01 2026-03-31 & goto :eof
if "%~3"=="" echo END date required. Usage: run html-custom 2026-01-01 2026-03-31 & goto :eof
%PYTHON% main.py html --period custom --start-date %~2 --end-date %~3
goto :eof

:clean
if exist output rmdir /s /q output
if exist logs rmdir /s /q logs
goto :eof

:uninstall
if exist %VENV% rmdir /s /q %VENV%
goto :eof
