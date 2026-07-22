@echo off
setlocal
title Maya MCP Installer

echo.
echo Maya MCP Installer
echo ------------------
echo This installs Maya MCP for your Windows user. Administrator access is not required.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-MayaMcp.ps1" %*
set "installer_exit=%ERRORLEVEL%"

echo.
if not "%installer_exit%"=="0" (
    echo Installation failed. Read the message above, fix the problem, and run this installer again.
) else (
    echo Installation complete. You can open Maya now.
)

if not defined MAYA_MCP_INSTALLER_NO_PAUSE pause
exit /b %installer_exit%
