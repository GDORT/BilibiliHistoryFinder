@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

echo ===================================================
echo   BilibiliHistoryFinder  -  Stop Server
echo ===================================================
echo.

set "PID="
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr "LISTENING" ^| findstr ":8765"') do set "PID=%%a"

if not defined PID goto NOTRUNNING

echo [Status] Found server on port 8765, PID = %PID%
taskkill /F /PID %PID% >nul 2>&1
if errorlevel 1 goto KILLFAIL

echo [OK] Server stopped.
goto DONE

:KILLFAIL
echo [Error] Failed to stop PID %PID%.
echo         Try running this script as Administrator.
goto DONE

:NOTRUNNING
echo [Status] No server is listening on port 8765 - nothing to stop.
goto DONE

:DONE
echo.
pause
endlocal
