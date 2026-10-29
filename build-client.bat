@echo off
rem Gera dist-client\UsagiGameClient\UsagiGameClient.exe (edicao cliente: so recebe o catalogo publicado).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || goto :erro
)
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :erro
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :erro
if exist client-data rmdir /s /q client-data
mkdir client-data\data
copy /y data\catalog.json client-data\data\catalog.json >nul
<nul set /p ="client" > client-data\data\edition.txt
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
    --name UsagiGameClient --distpath dist-client --workpath build-client ^
    --add-data "client-data\data;data" main.py || goto :erro
echo.
echo Pronto: dist-client\UsagiGameClient\UsagiGameClient.exe
pause
exit /b 0
:erro
echo Falhou. Confira se o Python 3.10+ esta instalado.
pause
