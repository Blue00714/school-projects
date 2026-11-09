@echo off
chcp 65001 >nul
set "PROJECT_PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PROJECT_PYTHON%" set "PROJECT_PYTHON=python"
"%PROJECT_PYTHON%" "%~dp0manage.py" serve --port 8777 --open-browser
pause
