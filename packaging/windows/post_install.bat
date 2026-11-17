@echo off
rem Runs once the MSI has unpacked benchhud (pyproject: post_install_script).
rem
rem Registers softcam.dll, the Windows virtual camera, so OBS and other camera
rem apps can find it (#124). Writing the registration takes administrator
rem rights. An install for all users runs this elevated, so it works. An
rem install for one user does not, so it fails here, quietly, and benchhud
rem offers the same step under Preferences, Output, with one approval prompt.
rem
rem Never fails the install: benchhud works without the virtual camera and
rem says in its status bar when it isn't registered.

set "DLL=%~dp0..\app\softcam.dll"
for %%I in ("%DLL%") do set "DLL=%%~fI"

rem No %DLL% inside a parenthesised block: a path holding a ")" would end it.
if not exist "%DLL%" echo benchhud: no softcam.dll in this install; virtual camera not registered
if not exist "%DLL%" exit /b 0

"%SystemRoot%\System32\regsvr32.exe" /s "%DLL%"
if errorlevel 1 echo benchhud: softcam.dll not registered; register it from Preferences, Output
if not errorlevel 1 echo benchhud: virtual camera registered
exit /b 0
