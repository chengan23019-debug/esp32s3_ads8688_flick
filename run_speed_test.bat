@echo off
setlocal
set PYTHON_EXE=C:\Users\PC006\.espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe

if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

echo ========================================================
echo   ESP32-S3 High-Speed USB Streaming Benchmark Test
echo ========================================================
echo.
"%PYTHON_EXE%" tools\test_usb_speed.py %*
pause
