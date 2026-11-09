@echo off
setlocal EnableDelayedExpansion
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

REM Se o servidor ja estiver rodando (atalho clicado duas vezes), so abre o navegador.
netstat -ano | findstr /r /c:":5000 .*LISTENING" >nul
if not errorlevel 1 (
    echo O sistema ja esta rodando. Abrindo o navegador...
    start "" http://127.0.0.1:5000/
    exit /b 0
)

REM So chama o pip se faltar alguma dependencia: antes ele rodava toda vez e,
REM sem internet, atrasava ou travava a inicializacao.
venv\Scripts\python.exe -c "import flask, flask_wtf, waitress, PIL, fpdf, openpyxl, qrcode" 2>nul
if errorlevel 1 (
    echo Instalando dependencias, aguarde...
    REM Primeiro tenta pela pasta "wheels" - pacotes ja baixados, sem internet. Se ela nao
    REM servir ^(ex.: outra versao do Python^), cai pra internet com as mesmas versoes.
    set INSTALOU=0
    if exist wheels (
        venv\Scripts\python.exe -m pip install -q --no-index --find-links wheels -r requirements.txt -c constraints.txt && set INSTALOU=1
    )
    if "!INSTALOU!"=="0" venv\Scripts\python.exe -m pip install -q -r requirements.txt -c constraints.txt && set INSTALOU=1
    if "!INSTALOU!"=="0" (
        echo.
        echo ERRO: nao foi possivel instalar as dependencias.
        echo Verifique a conexao com a internet e rode este arquivo de novo.
        pause
        exit /b 1
    )

    REM Alguns antivirus/politicas do Windows ^(Controle de Aplicativos^) bloqueiam
    REM arquivos .pyd compilados que o pip instala. Removemos os do fontTools
    REM ^(usado para gerar PDF^) para que ele use as versoes em Python puro.
    venv\Scripts\python.exe -c "import pathlib; [p.unlink() for p in pathlib.Path('venv/Lib/site-packages/fontTools').rglob('*.pyd')]" 2>nul
)

REM Senha nos backups (opcional): instala o pyzipper se faltar. Se nao der, o sistema
REM abre normalmente e so a senha dos backups fica indisponivel.
venv\Scripts\python.exe -m pip show -q pyzipper >nul 2>nul
if errorlevel 1 if exist wheels venv\Scripts\python.exe -m pip install -q --no-index --find-links wheels pyzipper -c constraints.txt >nul 2>nul

venv\Scripts\python.exe -c "import flask, flask_wtf, waitress, PIL, fpdf, openpyxl, qrcode"
if errorlevel 1 (
    echo.
    echo ERRO: as dependencias estao instaladas mas nao carregam ^(veja a mensagem acima^).
    echo Se aparecer algo sobre "bloqueado" ou "Controle de Aplicativos", o Windows
    echo esta impedindo um arquivo do Python de rodar - chame o suporte de TI.
    pause
    exit /b 1
)

REM A chave secreta assina os logins: quem a lesse poderia entrar como administrador.
REM Deixa so este usuario do Windows, os Administradores e o SISTEMA lerem o arquivo.
if exist .secret_key icacls .secret_key /inheritance:r /grant:r "%USERNAME%:F" "*S-1-5-32-544:F" "*S-1-5-18:F" >nul 2>&1

echo.
echo Iniciando o servidor do sistema de ponto...
REM "cmd /k" mantem a janela aberta se o servidor parar com erro, pra dar pra ler o motivo.
start "Servidor - SIGEP (nao feche esta janela)" cmd /k venv\Scripts\python.exe -m waitress --listen=0.0.0.0:5000 app:app

ping -n 3 127.0.0.1 >nul

start "" http://127.0.0.1:5000/

echo.
echo Tudo pronto. O totem foi aberto no navegador.
echo Para PARAR o sistema, feche a janela "Servidor - SIGEP".
echo Esta janela aqui ja pode ser fechada.
echo.
pause
