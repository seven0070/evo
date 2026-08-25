$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktop = Join-Path $repo "desktop"
$bridgeOut = Join-Path $desktop "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $bridgeOut | Out-Null

python -m pip install --upgrade pyinstaller platformdirs
python -m PyInstaller --clean --noconfirm --onefile --name evo-bridge --paths $repo --collect-submodules evo_agent --exclude-module setuptools --exclude-module pkg_resources (Join-Path $desktop "bridge\evo_desktop_bridge.py")
Copy-Item (Join-Path $repo "dist\evo-bridge.exe") (Join-Path $bridgeOut "evo-bridge-x86_64-pc-windows-msvc.exe") -Force

pnpm --dir $desktop install --frozen-lockfile
pnpm --dir $desktop run check
pnpm --dir $desktop exec tauri build
Write-Host "Windows installers are under desktop/src-tauri/target/release/bundle/"
