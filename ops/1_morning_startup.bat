@echo off
:: KUBERS — Morning Startup
:: Right-click → Run as administrator
setlocal
cd /d "C:\Kubers\ops"

echo.
echo ================================================
echo   KUBERS — Morning Startup
echo ================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 ( echo Run as administrator & pause & exit /b 1 )

:: 1. Fix IPv6
echo [1/5] Setting registered IPv6...
netsh interface ipv6 delete address "Wi-Fi" 2405:201:3d:5059:fce8:2cef:a2b8:9a43 >nul 2>&1
netsh interface ipv6 add address "Wi-Fi" 2405:201:3d:5059:e90d:78e1:b1c4:92a3 validlifetime=infinite preferredlifetime=infinite >nul 2>&1
for /f %%i in ('curl -s ifconfig.me') do set MYIP=%%i
echo       IP: %MYIP%
if not "%MYIP%"=="2405:201:3d:5059:e90d:78e1:b1c4:92a3" (
    echo   WARNING: IP mismatch. Re-run as admin.
    pause & exit /b 1
)

:: 2. Git pull latest fixes
echo [2/5] Pulling latest fixes from GitHub...
set "PATH=%PATH%;C:\Program Files\Git\cmd"
cd /d "C:\Kubers\engine"
git pull origin main
if %errorlevel% equ 0 ( python "C:\Kubers\ops\deployer.py" --pull )
cd /d "C:\Kubers\ops"

:: 3. Fetch token
echo.
echo [3/5] Fetching IndMoney token...
python fetch_token.py
if %errorlevel% neq 0 ( echo Token fetch failed. & pause & exit /b 1 )

:: 4. Start ops agent
echo [4/5] Starting ops agent (port 5003)...
start "Kubers Ops" /min python ops_agent.py
timeout /t 2 /nobreak >nul

:: 5. Start deployer
echo [5/5] Starting deployer...
start "Kubers Deployer" /min python deployer.py

echo.
echo ================================================
echo   Ready. In a new terminal:
echo     cd C:\Kubers\engine
echo     python kubers_calling.py
echo.
echo   If Claude needs live log access:
echo     cloudflared tunnel --url http://localhost:5003
echo   Share the trycloudflare.com URL with Claude.
echo ================================================
echo.
pause
