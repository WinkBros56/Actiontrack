@echo off
rem Run actiontrack from a source checkout without installing it.
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m actiontrack %*
exit /b %ERRORLEVEL%
