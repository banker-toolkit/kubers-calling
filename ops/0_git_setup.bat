@echo off
:: Kubers — Git Setup  (run ONCE)
:: Right-click → Run as administrator
setlocal enabledelayedexpansion
cd /d "C:\Kubers\engine"

echo.
echo ================================================
echo   Kubers — GitHub Setup
echo ================================================
echo.

set "PATH=%PATH%;C:\Program Files\Git\cmd"
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing Git via winget...
    winget install --id Git.Git -e --source winget --silent
    set "PATH=%PATH%;C:\Program Files\Git\cmd"
)
git --version

set /p GH_USER="Enter your GitHub username: "
set REPO_NAME=kubers-calling

git config --global user.name "%GH_USER%"
git config --global user.email "%GH_USER%@users.noreply.github.com"
git config --global init.defaultBranch main
git config --global core.autocrlf true

:: SSH key
set SSH_KEY=%USERPROFILE%\.ssh\id_ed25519_kubers
if not exist "%SSH_KEY%" (
    ssh-keygen -t ed25519 -C "kubers-trading-pc" -f "%SSH_KEY%" -N ""
)
net start ssh-agent >nul 2>&1
ssh-add "%SSH_KEY%" >nul 2>&1

:: Write SSH config
echo. >> "%USERPROFILE%\.ssh\config"
echo Host github.com >> "%USERPROFILE%\.ssh\config"
echo     HostName github.com >> "%USERPROFILE%\.ssh\config"
echo     User git >> "%USERPROFILE%\.ssh\config"
echo     IdentityFile %SSH_KEY% >> "%USERPROFILE%\.ssh\config"
echo     IdentitiesOnly yes >> "%USERPROFILE%\.ssh\config"

echo.
echo ================================================
echo   ADD THIS KEY TO GITHUB NOW
echo   https://github.com/settings/ssh/new
echo   Title: kubers-trading-pc
echo ================================================
echo.
type "%SSH_KEY%.pub"
echo.
pause

:: Init repo
if not exist ".git" ( git init )
if not exist ".gitignore" (
    (
        echo investright_creds.json
        echo *.db
        echo *.db-wal
        echo *.db-shm
        echo *.log
        echo *.bak*
        echo __pycache__/
        echo deploy/
        echo *.pyc
        echo trade_log_*.csv
        echo signal_log_*.csv
    ) > .gitignore
)
git add -A
git commit -m "Initial commit — Kubers v8" --allow-empty

echo.
echo ================================================
echo   CREATE GITHUB REPO NOW
echo   https://github.com/new
echo   Name: kubers-calling  |  PRIVATE  |  Empty
echo ================================================
echo.
pause

set REPO_URL=git@github.com:%GH_USER%/kubers-calling.git
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%
git branch -M main
git push -u origin main

:: Add github_user and repo to creds file
python -c "import json,pathlib; f=pathlib.Path('investright_creds.json'); d=json.loads(f.read_text()) if f.exists() else {}; d.update({'github_user':'%GH_USER%','github_repo':'kubers-calling','github_token':''}); f.write_text(json.dumps(d,indent=2)); print('Creds updated — add your GitHub PAT to investright_creds.json')"

:: Schedule auto-pull at 15:25
schtasks /delete /tn "KubersAutoPull" /f >nul 2>&1
schtasks /create /tn "KubersAutoPull" /tr "C:\Kubers\ops\auto_pull.bat" /sc daily /st 15:25 /ru "%USERNAME%" /rl highest /f
echo Auto-pull scheduled at 15:25 daily.

echo.
echo ================================================
echo   Done. Next: add your GitHub PAT to
echo   C:\Kubers\engine\investright_creds.json
echo   field: "github_token"
echo   Get one at: https://github.com/settings/tokens/new
echo   Scope: repo (only)
echo ================================================
echo.
pause
