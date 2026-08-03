@echo off
setlocal
cd /d %~dp0

if not exist venv (
    echo Preparando o sistema pela primeira vez, aguarde alguns instantes...
    py -3 -m venv venv
    if errorlevel 1 (
        echo.
        echo ERRO: nao foi possivel criar o ambiente Python.
        echo Verifique se o Python esta instalado ^(python.org^) e tente novamente.
        pause
        exit /b 1
    )
)

echo Instalando/atualizando dependencias, aguarde...
venv\Scripts\python.exe -m pip install -q --upgrade pip
venv\Scripts\python.exe -m pip install -q -r requirements.txt

REM Alguns antivirus/politicas do Windows (Controle de Aplicativos) bloqueiam
REM arquivos .pyd compilados que o pip instala. Removemos os do fontTools
REM (usado para gerar PDF) para que ele use as versoes em Python puro, que
REM ja vem junto e funcionam identico, só um pouco mais devagar.
venv\Scripts\python.exe -c "import pathlib; [p.unlink() for p in pathlib.Path('venv/Lib/site-packages/fontTools').rglob('*.pyd')]" 2>nul

echo.
echo Iniciando o servidor do sistema de ponto...
start "Servidor - Sistema de Ponto (nao feche esta janela)" venv\Scripts\python.exe -m waitress --listen=0.0.0.0:5000 app:app

ping -n 3 127.0.0.1 >nul

start "" http://127.0.0.1:5000/

echo.
echo Tudo pronto! O totem foi aberto no navegador.
echo Para PARAR o sistema, feche a janela "Servidor - Sistema de Ponto".
echo Esta janela aqui ja pode ser fechada.
echo.
pause
