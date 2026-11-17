# Build softcam.dll, the Windows virtual camera benchhud publishes through (#124).
#
# softcam (https://github.com/tshino/softcam, MIT) is a DirectShow filter: once
# registered, OBS and other camera apps list it as "DirectShow Softcam". It is
# built here from a pinned commit instead of being kept in the repo as a binary,
# so the DLL in the installer is one CI compiled from source anyone can read.
#
#   pwsh packaging/windows/build-softcam.ps1
#
# Needs Visual Studio 2022 (or its Build Tools) with the C++ desktop workload,
# and git. Leaves softcam.dll in packaging/windows/softcam/, where pyproject's
# Windows sources pick it up for the MSI.

param(
    [string]$OutDir = (Join-Path $PSScriptRoot 'softcam')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# softcam v1.8.1. A commit, not the tag: a tag can be moved, a commit can't.
$Repo = 'https://github.com/tshino/softcam.git'
$Commit = 'afb424c2415b04da627fa1a7b4706575c2ba3010'

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

$work = Join-Path ([IO.Path]::GetTempPath()) "softcam-$Commit"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }

Invoke-Checked 'git init' { git init --quiet $work }
Invoke-Checked 'git remote add' { git -C $work remote add origin $Repo }
Invoke-Checked 'git fetch' { git -C $work fetch --quiet --depth 1 origin $Commit }
Invoke-Checked 'git checkout' { git -C $work -c advice.detachedHead=false checkout --quiet FETCH_HEAD }
$head = (git -C $work rev-parse HEAD).Trim()
if ($head -ne $Commit) { throw "softcam checkout is at $head, expected $Commit" }
Write-Host "softcam source at $head"

# Link the C runtime into the DLL. Upstream links it dynamically, which makes
# softcam.dll need the Visual C++ Redistributable in every process that loads
# it: regsvr32, benchhud and the camera app. A machine without it would fail to
# register with nothing to say why. Two changes do it: /MT for the compiler,
# and dropping the import library for the DLL runtime, which the project files
# name outright. The check after the build fails if either stops working.
foreach ($project in Get-ChildItem -Path (Join-Path $work 'src') -Recurse -Filter '*.vcxproj') {
    $xml = Get-Content -Raw -Path $project.FullName
    $patched = $xml.Replace('msvcrt.lib;', '')
    if ($patched -ne $xml) { Set-Content -NoNewline -Path $project.FullName -Value $patched }
}
$env:_CL_ = '/MT'

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found: is Visual Studio (or its Build Tools) installed?" }
$msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $msbuild) { throw 'MSBuild not found' }

# Only the softcam project and what it references: the solution's test
# projects want NuGet packages this build has no use for.
Invoke-Checked 'msbuild' {
    & $msbuild (Join-Path $work 'softcam.sln') /m /nologo /verbosity:minimal `
        /t:softcam /p:Configuration=Release /p:Platform=x64
}

$dll = Join-Path $work 'dist\bin\x64\softcam.dll'
if (-not (Test-Path $dll)) { throw "the build finished but $dll is not there" }

$dumpbin = & $vswhere -latest -products * -find 'VC\Tools\MSVC\**\bin\Hostx64\x64\dumpbin.exe' | Select-Object -First 1
if (-not $dumpbin) { throw 'dumpbin.exe not found' }

$dependents = (& $dumpbin /nologo /dependents $dll) -join "`n"
Write-Host $dependents
if ($dependents -match '(?i)vcruntime|msvcp|ucrtbase|api-ms-win-crt') {
    throw 'softcam.dll still needs the Visual C++ runtime; the static link did not take'
}

$exports = (& $dumpbin /nologo /exports $dll) -join "`n"
foreach ($name in 'scCreateCamera', 'scDeleteCamera', 'scSendFrame', 'scIsConnected', 'DllRegisterServer', 'DllUnregisterServer') {
    if ($exports -notmatch "\b$name\b") { throw "softcam.dll does not export $name" }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Copy-Item -Force $dll (Join-Path $OutDir 'softcam.dll')
$built = Get-Item (Join-Path $OutDir 'softcam.dll')
$hash = (Get-FileHash -Algorithm SHA256 $built.FullName).Hash.ToLower()
Write-Host "built $($built.FullName) ($($built.Length) bytes, sha256 $hash)"
