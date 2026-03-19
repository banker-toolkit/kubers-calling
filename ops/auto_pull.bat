@echo off
cd /d "C:\Kubers\engine"
set "PATH=%PATH%;C:\Program Files\Git\cmd"
echo [%TIME%] Auto-pull starting...
git pull origin main
if %errorlevel%==0 (
    python deployer.py --pull
    python full_analysis.py >> "C:\Kubers\logs\analysis_%DATE:/=-%_log.txt" 2>&1
    echo [%TIME%] Done.
) else (
    echo [%TIME%] git pull FAILED
)
