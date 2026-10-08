@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

echo ===================================================
echo   BilibiliHistoryFinder  -  Stop Server
echo ===================================================
echo.

rem Drop the handshake flag FIRST, before any kill. start.bat sees it and closes
rem its own window silently instead of pausing - the stop was intentional, there
rem is no error for a human to read.
> "%~dp0data\run\stop.flag" echo stop

set "PID="
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr "LISTENING" ^| findstr ":8765"') do set "PID=%%a"

if not defined PID goto NOTRUNNING

echo [Status] Found server on port 8765, PID = %PID%
taskkill /F /PID %PID% >nul 2>&1
if errorlevel 1 goto KILLFAIL

echo [OK] Server stopped.
goto ANALYZER

rem Only a real failure pauses - a normal stop must close the window by itself.
:KILLFAIL
echo.
echo [Error] Failed to stop PID %PID%.
echo         Try running this script as Administrator.
echo.
pause
goto END

:NOTRUNNING
echo [Status] No server is listening on port 8765 - nothing to stop.

rem Analyzer (started by start.bat) - matched by image name, not by port,
rem because it may already be dying on its own and stop when a live.
:ANALYZER
rem TASKLIST output goes to a temp file, then FINDSTR tests it.
rem Two traps this avoids:
rem   1) ">" discarding the very output we need to test (errorlevel would always be 0)
rem   2) piping into find -> depends on which find.exe resolves first
rem The image name is matched without wildcards; tasklist truncates long names in
rem its own table but the /FI filter is what guarantees a hit only when present.
set "TASKOUT=%TEMP%\bhf_tl.txt"
tasklist /NH /FO CSV /FI "IMAGENAME eq BilibiliHistoryAnalyzer.exe" >"%TASKOUT%" 2>&1
findstr "BilibiliHistoryAnalyzer.exe" "%TASKOUT%" >nul
if errorlevel 1 goto DANALYZER
del /q "%TASKOUT%" >nul 2>&1
echo [Analyzer] Stopping BilibiliHistoryAnalyzer.exe...
taskkill /F /IM BilibiliHistoryAnalyzer.exe >nul 2>&1
echo [OK] Analyzer stopped.
goto DONE

:DANALYZER
del /q "%TASKOUT%" >nul 2>&1
echo [Analyzer] Not running - nothing to stop.

:DONE
endlocal
