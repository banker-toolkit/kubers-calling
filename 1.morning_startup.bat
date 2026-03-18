@echo off
:: KUBER'S CALLING — Morning Startup
:: Run this ONCE before starting the dashboard each day
:: Right-click → Run as administrator

echo.
echo ================================================
echo   KUBER'S CALLING — Morning IP Setup
echo ================================================
echo.

:: Check admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Please right-click this file and Run as administrator
    echo.
    pause
    exit /b 1
)

echo [1/3] Removing Windows hardware IPv6 address...
netsh interface ipv6 delete address "Wi-Fi" 2405:201:3d:5059:fce8:2cef:a2b8:9a43 >nul 2>&1
echo       Done.

echo [2/3] Adding INDmoney registered IPv6 address...
netsh interface ipv6 add address "Wi-Fi" 2405:201:3d:5059:e90d:78e1:b1c4:92a3 validlifetime=infinite preferredlifetime=infinite >nul 2>&1
echo       Done.

echo [3/3] Verifying outbound IP...
for /f %%i in ('curl -s ifconfig.me') do set MYIP=%%i
echo       Current IP: %MYIP%

echo.
if "%MYIP%"=="2405:201:3d:5059:e90d:78e1:b1c4:92a3" (
    echo   OK  IP matches INDmoney registered address
    echo.
    echo   NEXT STEPS:
    echo   1. Generate a fresh token in INDmoney browser NOW
    echo   2. Run: python kubers_calling.py
    echo   3. Paste token into dashboard - CONNECT
) else (
    echo   WARNING: IP is %MYIP% - not the registered address
    echo   Try running this script again, or check network connection
)

echo.
pause
