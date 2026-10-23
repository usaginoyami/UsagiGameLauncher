@echo off
rem Abre o Usagi GameLaucher a partir do codigo-fonte.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    py -3 -m venv .venv || goto :erro
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :erro
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :erro
)
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0
:erro
echo Falhou. Confira se o Python 3.10+ esta instalado (https://www.python.org/downloads/).
pause
