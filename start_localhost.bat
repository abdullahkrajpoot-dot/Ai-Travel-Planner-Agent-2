@echo off
title Streamlit Localhost - AI Travel Planner
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\ALI BABA TRAVEL\AppData\Local\Programs\Python\Python311\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Python 3.11 was not found at:
    echo %PYTHON_EXE%
    echo.
    echo Trying the default python command instead...
    set "PYTHON_EXE=python"
)

echo Starting Streamlit on http://127.0.0.1:8501
echo Project: %CD%
echo.
"%PYTHON_EXE%" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless false

echo.
echo Streamlit stopped or failed to start.
pause
