@echo off
set "VS_SETUP=C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe"
set "VS_CONFIG=%~dp0..\..\work\wechat_hook_build.vsconfig"

if not exist "%VS_SETUP%" (
  echo Visual Studio Installer not found.
  pause
  exit /b 1
)

"%VS_SETUP%" modify --installPath "C:\Program Files\Microsoft Visual Studio\18\Community" --config "%VS_CONFIG%"
