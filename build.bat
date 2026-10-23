@echo off
rem Gera dist\UsagiGameLauncher\UsagiGameLauncher.exe (pasta portatil, sem instalar Python).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || goto :erro
)
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :erro
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :erro
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
    --name UsagiGameLauncher ^
    --add-data "data;data" ^
    main.py || goto :erro
echo.
echo Pronto: dist\UsagiGameLauncher\UsagiGameLauncher.exe
pause
exit /b 0
:erro
echo Falhou. Confira se o Python 3.10+ esta instalado.
pause
