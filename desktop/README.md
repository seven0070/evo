# Evo desktop application

This directory contains the Windows-first Tauri desktop shell for Evo. The UI is a local static frontend; it does not call providers, read arbitrary files, or execute commands directly. Tauri invokes the narrow `desktop/bridge/evo_desktop_bridge.py` process, which reuses Evo's existing `AgentRuntime`, personal profile, SQLite persistence, approval, Kernel, and Verification APIs.

## Development

From the repository root:

```bash
cd desktop
pnpm check
cd src-tauri
cargo check
```

On Linux, Tauri compile-checking requires the Rust toolchain and WebKitGTK development packages. A native Windows installer must be built on Windows (or by the repository workflow), because the final application bundles a Windows Python sidecar.

## Windows packaging

The Windows build packages `evo-bridge.exe` as a Tauri sidecar. The sidecar is built from the existing Python bridge with PyInstaller and is placed at `desktop/src-tauri/binaries/evo-bridge-x86_64-pc-windows-msvc.exe` before running the Tauri bundler. See `scripts/build_windows.ps1` and `.github/workflows/desktop-windows.yml`.

For a local development run, the Rust shell falls back to the Python bridge and uses `EVO_PYTHON` when set. The default workspace is `%USERPROFILE%\\EvoWorkspace`; set `EVO_WORKSPACE` to choose another dedicated workspace.

## Safety contract

The desktop UI is an operator surface, not a second agent authority. Goal execution remains bounded by the personal profile and Runtime. Approval is human-only. Safe mode and the kill switch remain existing Runtime controls. Verification remains the only source of success claims. External actions remain disabled by the default personal profile. Tauri capabilities grant only core window/runtime access; no remote URL, filesystem, shell, or credential permission is declared.

The first desktop release intentionally focuses on goal entry, Runtime status, approval queue, verified history, safe mode, and the kill switch. Strategic, evolution, promotion, rollback, and deep diagnostics remain available through the existing CLI until dedicated UI surfaces are justified by real personal use.
