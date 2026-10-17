@echo off
rem Builds dist\RivalsBot\RivalsBot.exe - double-click this file to build.
rem The exe always starts as Administrator (Windows will show a UAC prompt).
setlocal
cd /d "%~dp0"

echo Installing build dependencies...
python -m pip install --upgrade -r requirements.txt pyinstaller || goto :error

echo Building RivalsBot.exe...
python -m PyInstaller --noconfirm --clean --onefile --console --uac-admin ^
    --name RivalsBot ^
    --collect-all windows_capture ^
    --distpath dist\RivalsBot --workpath build --specpath build ^
    rivals_bot.py || goto :error

echo Copying images and config...
xcopy /E /I /Y templates dist\RivalsBot\templates >nul || goto :error
if exist config.json copy /Y config.json dist\RivalsBot\config.json >nul

echo.
echo Done: dist\RivalsBot\RivalsBot.exe
echo Keep the templates folder and config.json next to the exe.
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
