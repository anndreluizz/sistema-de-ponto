@echo off
setlocal
cd /d %~dp0

if not exist venv (
    echo O sistema ainda nao foi instalado neste computador ^(pasta venv nao encontrada^).
    echo Rode o iniciar.bat pelo menos uma vez antes de usar este script.
    pause
    exit /b 1
)

venv\Scripts\python.exe resetar_senha_emergencia.py

pause
