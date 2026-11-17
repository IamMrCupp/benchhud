@echo off
rem Runs before the MSI removes benchhud (pyproject: pre_uninstall_script).
rem
rem Takes the Windows virtual camera's registration back out, so camera apps
rem aren't left listing a camera whose DLL has gone (#124). Only when the
rem registration is this install's own softcam.dll: another program can ship
rem softcam too, and its registration is not ours to remove.
rem
rem Never fails the uninstall.

set "DLL=%~dp0..\app\softcam.dll"
for %%I in ("%DLL%") do set "DLL=%%~fI"
set "KEY=HKCR\CLSID\{AEF3B972-5FA5-4647-9571-358EB472BC9E}\InprocServer32"

if not exist "%DLL%" exit /b 0

rem No %DLL% inside a parenthesised block: a path holding a ")" would end it.
"%SystemRoot%\System32\reg.exe" query "%KEY%" /ve /reg:64 2>nul | "%SystemRoot%\System32\findstr.exe" /l /i /c:"%DLL%" >nul
if errorlevel 1 echo benchhud: the registered virtual camera is not this install's; left alone
if errorlevel 1 exit /b 0

"%SystemRoot%\System32\regsvr32.exe" /u /s "%DLL%"
if errorlevel 1 echo benchhud: could not unregister the virtual camera
if not errorlevel 1 echo benchhud: virtual camera unregistered
exit /b 0
