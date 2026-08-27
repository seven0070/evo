param(
    [switch]$SidecarOnly
)

$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktop = Join-Path $repo "desktop"
$bridgeOut = Join-Path $desktop "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $bridgeOut | Out-Null

python -m pip install --upgrade "pyinstaller==6.22.2" "platformdirs==4.11.4"
python -m PyInstaller --clean --noconfirm --onefile --name evo-bridge --paths $repo --collect-submodules evo_agent --exclude-module setuptools --exclude-module pkg_resources (Join-Path $desktop "bridge\evo_desktop_bridge.py")
Copy-Item (Join-Path $repo "dist\evo-bridge.exe") (Join-Path $bridgeOut "evo-bridge-x86_64-pc-windows-msvc.exe") -Force
if (-not (Test-Path (Join-Path $bridgeOut "evo-bridge-x86_64-pc-windows-msvc.exe"))) {
    throw "Windows Tauri sidecar was not created at the required target-triple path"
}
Write-Host "Windows Tauri sidecar: $bridgeOut\evo-bridge-x86_64-pc-windows-msvc.exe"
if ($SidecarOnly) {
    Write-Host "Sidecar-only build requested; skipping Tauri bundle generation."
    exit 0
}
Push-Location $desktop
try {
    pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }
    pnpm run check
    if ($LASTEXITCODE -ne 0) { throw "desktop static check failed with exit code $LASTEXITCODE" }
    pnpm exec tauri build --bundles nsis,msi
    if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS/MSI build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$bundle = Join-Path $desktop "src-tauri\target\release\bundle"
$artifacts = @(Get-ChildItem -Path (Join-Path $bundle "nsis\*.exe"), (Join-Path $bundle "msi\*.msi") -File | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    [ordered]@{ name = $_.Name; path = $_.FullName.Substring($repo.Path.Length + 1); bytes = $_.Length; sha256 = $hash }
})
$manifest = [ordered]@{
    product = "Evo"
    version = "1.0.0"
    commit = (git -C $repo rev-parse HEAD)
    python = (python --version)
    rust = (rustc --version)
    node = (node --version)
    tauri_cli = (pnpm --dir $desktop exec tauri --version)
    pyinstaller = "6.22.2"
    platformdirs = "4.11.4"
    artifacts = $artifacts
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $bundle "release-manifest.json") -Encoding UTF8
Write-Host "Windows installers and release-manifest.json are under desktop/src-tauri/target/release/bundle/"
