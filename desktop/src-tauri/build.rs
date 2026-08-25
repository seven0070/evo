use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    // Tauri validates every `externalBin` entry even for `cargo check`. Keep the
    // Linux developer checkout self-contained without committing or packaging a
    // fake Windows binary; the Windows build script replaces its own target.
    if env::var("TARGET").is_ok_and(|target| target.contains("linux")) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let shim = manifest_dir.join("binaries/evo-bridge-x86_64-unknown-linux-gnu");
        if !shim.exists() {
            let repo = manifest_dir
                .join("../..")
                .canonicalize()
                .expect("repository root must exist");
            let bridge = repo.join("desktop/bridge/evo_desktop_bridge.py");
            let content = format!(
                "#!/bin/sh\nPYTHONPATH=\"{}${{PYTHONPATH:+:$PYTHONPATH}}\" exec \"${{EVO_PYTHON:-python3}}\" \"{}\" \"$@\"\n",
                repo.display(),
                bridge.display()
            );
            fs::write(&shim, content).expect("write Linux development bridge shim");
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let mut permissions = fs::metadata(&shim)
                    .expect("read shim metadata")
                    .permissions();
                permissions.set_mode(0o755);
                fs::set_permissions(&shim, permissions).expect("make Linux bridge shim executable");
            }
        }
    }
    tauri_build::build()
}
