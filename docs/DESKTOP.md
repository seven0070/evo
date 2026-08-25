# Evo Desktop V1

Evo Desktop V1 is a Windows-first Tauri shell over the existing local Evo Runtime. It is intentionally a presentation and operator surface, not a second agent implementation.

## Trust boundary

```text
Desktop UI
  -> Tauri command allowlist
  -> one bounded local bridge process per command
  -> existing PersonalOperatingProfile / Governance
  -> existing AgentRuntime
  -> existing Kernel, registered tools, Verifier, and SQLite persistence
```

The static UI can display profile, Runtime status, pending approvals, and bounded task history. It can submit one user goal, request one bounded Runtime cycle, enable safe mode, activate the existing kill switch, or record a human approval against the exact Runtime scope hash. It cannot call a tool directly, run arbitrary Python or shell, read arbitrary paths, mutate connectors, access credentials, approve itself, promote an evolution, clear the kill switch, or change the protected core.

The four-second UI refresh is status polling only. It never submits work or invokes `run_cycle`. A goal submission is the only UI action that requests one bounded cycle, and that request remains subject to the existing Runtime ceilings and verification authority.

## Personal defaults

The bridge loads the personal operating profile from `<workspace>/.evo/personal_profile.json` when present. Without a custom profile, the safe offline defaults are used: one concurrent task, one task per cycle, bounded task and total Runtime durations, bounded retries/recovery/replanning, a confined workspace, an existing shell allowlist, explicit medium/high/critical approval, and external actions disabled. The UI does not expose profile weakening or credential configuration.

On Windows the default workspace is `%USERPROFILE%\\EvoWorkspace`. On Linux and macOS development runs it is `$HOME/EvoWorkspace`. Set `EVO_WORKSPACE` to use another dedicated workspace. The bridge stores authoritative state in `<workspace>/.evo/agent.sqlite3`, using the same Runtime persistence as the CLI.

## Approval and execution flow

A user enters a goal and may select the optional approval requirement. The UI submits the goal to Runtime and requests one bounded cycle. If Runtime creates an approval request, the UI displays its task identifier, reason, and scope hash. The operator must press **Approve once**; the bridge always passes the actor as `human` and Runtime revalidates the stored scope hash. The UI does not accept an arbitrary actor or approval scope.

After approval, the operator may request another bounded cycle. The UI history displays only a shaped summary of task state and verification, while full authoritative task records remain in SQLite. Verification is the source of success claims; a task is not treated as authoritatively successful merely because execution returned.

## Development

Install Rust, Node.js, pnpm, Python 3.11 or newer, and the Tauri host dependencies for the development operating system. From the repository root run:

```bash
python3 -m compileall -q evo_agent desktop/bridge
cd desktop
pnpm install --frozen-lockfile
pnpm check
cd src-tauri
cargo fmt --check
cargo check
cargo clippy -- -D warnings
```

The bridge can be exercised without launching a window:

```bash
cd /path/to/evo
PYTHONPATH=. printf '%s\n' '{"command":"get_profile"}' \\
  | python3 desktop/bridge/evo_desktop_bridge.py --workspace /tmp/evo-desktop-test
```

The complete bridge regression suite is `tests/test_desktop_bridge.py`. The existing Python regression suite remains authoritative and should be run after desktop changes:

```bash
PYTHONPATH=. pytest -q
```

## Standalone Windows packaging

A true standalone Windows build bundles the Evo core inside a PyInstaller sidecar named `evo-bridge.exe`; it does not require a separate Python installation at runtime. The sidecar is placed at `desktop/src-tauri/binaries/evo-bridge-x86_64-pc-windows-msvc.exe` and is bundled by Tauri as `externalBin: ["binaries/evo-bridge"]`. The Windows Rust shell prefers that packaged executable and falls back to the Python bridge only for development or an explicitly configured `EVO_BRIDGE_PATH`.

Build the Windows installers on Windows or through the repository workflow:

```powershell
./scripts/build_windows.ps1
```

The script installs PyInstaller and `platformdirs`, freezes the bridge plus all `evo_agent` submodules, runs the static check, and builds both NSIS and MSI targets. Installers are written below `desktop/src-tauri/target/release/bundle/`. The GitHub Actions workflow `.github/workflows/desktop-windows.yml` runs on `workflow_dispatch` and version tags and uploads the NSIS/MSI artifacts.

The current Linux environment cannot produce or validate a Windows PE executable or installer. It does validate the equivalent frozen bridge path locally and builds a native AppImage packaging smoke test. Windows installer generation and first-run validation therefore remain CI/Windows acceptance steps until a Windows runner completes successfully.

## Configuration and limitations

The first UI deliberately does not expose evolution administration, benchmark controls, promotion, rollback, provider credentials, external integrations, arbitrary diagnostics, or unrestricted shell access. External actions remain disabled by the personal profile. The application has no localhost HTTP server and no resident hidden agent loop; each Tauri command starts one short-lived bridge process, and the UI refresh timer only reads state.

The desktop project is developed under `/home/ubuntu/evo` in this task. It is not deployed to the user's local Windows machine automatically. After a Windows installer is produced, the operator must install it on the target machine and select or configure the intended dedicated workspace according to the release instructions.

## Release checklist

Before considering a desktop release, run the static check, bridge tests, full Python regression suite, Rust formatting/check/lint, `git diff --check`, `scripts/validate_v1.py`, and `scripts/run_v1_readiness.py`. Confirm that the protected-core digest checks and production immutability checks pass. On Windows, run `scripts/build_windows.ps1`, install the resulting NSIS or MSI artifact in a clean user profile, verify the packaged sidecar can answer `get_profile`, and manually exercise goal submission, approval, verified history, safe mode, and the kill switch.
