$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Version = 'v26.10.19.02'
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [System.Runtime.InteropServices.Architecture]::X64) {
    throw 'This build must run on native Windows x64.'
}

foreach ($folder in @('build', 'dist', 'release')) {
    if (Test-Path -LiteralPath $folder) { Remove-Item -LiteralPath $folder -Recurse -Force }
}
New-Item -ItemType Directory -Path 'release' | Out-Null
python -m PyInstaller --noconfirm --clean HV_P2P_NMS.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$Executable = Join-Path $Root 'dist/HV P2P NMS.exe'
if (-not (Test-Path -LiteralPath $Executable)) { throw 'Missing compiled Windows executable' }
python scripts/verify_pe_arch.py $Executable
if ($LASTEXITCODE -ne 0) { throw 'PE architecture check failed' }

# Run self-test on the actual frozen EXE (tests Qt/python bundling).
$Process = Start-Process -FilePath $Executable -ArgumentList '--self-test' -Wait -PassThru
if ($Process.ExitCode -ne 0) { throw "Frozen Windows app self-test failed (exit $($Process.ExitCode))" }

$Stem = "HV P2P NMS ${Version} Windows x64"
$OutputExe = Join-Path $Root "release/$Stem.exe"
$Zip = Join-Path $Root "release/$Stem.zip"
Copy-Item -LiteralPath $Executable -Destination $OutputExe
Compress-Archive -LiteralPath $OutputExe -DestinationPath $Zip -Force
$HashOutput = Join-Path $Root 'release/SHA256_Windows_x64.txt'
$Hashes = @($OutputExe, $Zip) | ForEach-Object {
    $h = Get-FileHash -LiteralPath $_ -Algorithm SHA256
    "$($h.Hash.ToLowerInvariant())  $(Split-Path $_ -Leaf)"
}
[System.IO.File]::WriteAllLines($HashOutput, $Hashes)
Get-ChildItem 'release' | Select-Object Name, Length
