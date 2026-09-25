@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo   BilibiliHistoryFinder  -  Start Server
echo ===================================================
echo.

set "PYEXE="

rem --- 1) manual override: set BHF_PYTHON=C:\path\to\python.exe
if defined BHF_PYTHON if exist "%BHF_PYTHON%" set "PYEXE=%BHF_PYTHON%"

rem --- 2) real python on PATH, skipping the 0-byte Microsoft Store alias
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PYEXE set "PYEXE=%%P"

rem --- 3) WorkBuddy managed python
if not defined PYEXE for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if not defined PYEXE if exist "%%~fD\python.exe" set "PYEXE=%%~fD\python.exe"

rem --- 4) py launcher
if not defined PYEXE if exist "%SystemRoot%\py.exe" set "PYEXE=%SystemRoot%\py.exe"

rem --- 5) common install locations
if not defined PYEXE for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "C:\Python313\python.exe" "C:\Python312\python.exe") do if not defined PYEXE if exist %%P set "PYEXE=%%~P"

if not defined PYEXE goto NOPYTHON

"%PYEXE%" --version >nul 2>&1
if errorlevel 1 goto BADPYTHON

set "PID="
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr "LISTENING" ^| findstr ":8765"') do set "PID=%%a"

if defined PID goto MONITOR

echo [Python] %PYEXE%
echo [Status] Starting server in THIS window (supervised mode).
echo          Keep this window OPEN - closing it stops the server.
echo          URL    : http://127.0.0.1:8765
echo          Restart: use the "6 Apply changes" button in the web UI
echo                   (no need to reopen this window; the supervisor relaunches it)
echo          Stop   : press Ctrl+C here, or run stop.bat
echo ---------------------------------------------------

rem Tell the server it is supervised, so the web UI can offer one-click restart.
set "BHF_SUPERVISED=1"

:RUN
"%PYEXE%" "%~dp0src\server.py"
set "RC=%errorlevel%"

rem --- Relaunch decision (this block writes nothing to the console) -------------
rem 1) AUTHORITATIVE: handshake file. Before exiting on a web-UI restart the server
rem    drops data\run\relaunch.flag. We trust the FILE, not the exit code - Windows
rem    and the Python interpreter can lose the exit code (observed: it arrived as 0
rem    because the interpreter tore down before the daemon worker called os._exit,
rem    so this check must not depend on %RC% alone).
if exist "%~dp0data\run\relaunch.flag" goto RESTART
rem 2) Legacy signal: exit code 42.
if "%RC%"=="42" goto RESTART

echo ---------------------------------------------------
echo [Status] Server process ended. Exit code = %RC%
if "%RC%"=="9009" echo          9009 = python could not be executed - see message above.
echo.
echo This window stays open so you can read any error above.
echo.
pause
goto END

:RESTART
rem ---------------------------------------------------------------------------
rem Restart requested from the web UI (handshake file, or exit code 42).
rem This block DELIBERATELY writes nothing to the console.
rem Why: when a Windows console is in QuickEdit "selected" state, ANY write to it
rem blocks until the selection is cancelled - the supervisor's own echo would then
rem stall the relaunch chain. Delay is done with ping (no console I/O at all).
rem Trail of this chain is written by the server to data\run\restart.log.
rem ---------------------------------------------------------------------------
del /q "%~dp0data\run\relaunch.flag" >nul 2>&1
ping -n 3 -w 500 127.0.0.1 >nul 2>&1
goto RUN

:MONITOR
echo [Python] %PYEXE%
echo [Status] Server is ALREADY running.  PID = %PID%
echo          URL   : http://127.0.0.1:8765
echo          Live status refreshes every 10s below.
echo          Closing this window does NOT stop the server.
echo          To stop the SERVER itself, run stop.bat
echo ---------------------------------------------------
where curl >nul 2>&1
if errorlevel 1 goto LOOPPLAIN

:LOOP
<nul set /p "=[%time:~0,8%] "
curl -s --max-time 3 http://127.0.0.1:8765/api/rules-status
echo.
timeout /t 10 /nobreak >nul
goto LOOP

:LOOPPLAIN
echo [%time:~0,8%] server PID %PID% alive
timeout /t 10 /nobreak >nul
goto LOOPPLAIN

:NOPYTHON
echo [Error] No usable Python 3 was found on this machine.
echo.
echo         Looked in:
echo           - PATH  (Microsoft Store alias is ignored on purpose)
echo           - %%USERPROFILE%%\.workbuddy\binaries\python\versions\*
echo           - %%LOCALAPPDATA%%\Programs\Python\Python31x
echo           - C:\Python31x  and  %%SystemRoot%%\py.exe
echo.
echo         Fix A: install Python 3 from https://www.python.org/downloads/
echo                and tick "Add python.exe to PATH" during setup.
echo         Fix B: set BHF_PYTHON to a working python.exe, for example
echo                setx BHF_PYTHON "C:\Python313\python.exe"
echo                then open a NEW window and run start.bat again.
echo.
pause
goto END

:BADPYTHON
echo [Error] Python was located but could not run:
echo         %PYEXE%
echo         It is probably a broken Microsoft Store alias placeholder.
echo         Install a real Python 3, or set BHF_PYTHON to a working python.exe
echo.
pause
goto END

:END
endlocal
