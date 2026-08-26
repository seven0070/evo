#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tauri::{AppHandle, Manager, State};

struct AppState {
    workspace: PathBuf,
    bridge: PathBuf,
}

fn default_workspace(app: &AppHandle) -> PathBuf {
    if let Ok(value) = env::var("EVO_WORKSPACE") {
        return PathBuf::from(value).join(".").to_path_buf();
    }
    if let Ok(app_data) = app.path().app_data_dir() {
        return app_data.join("workspace");
    }
    let home = if cfg!(windows) {
        env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string())
    } else {
        env::var("HOME").unwrap_or_else(|_| ".".to_string())
    };
    PathBuf::from(home).join("EvoWorkspace")
}

fn resolve_bridge(app: &AppHandle) -> PathBuf {
    if let Ok(value) = env::var("EVO_BRIDGE_PATH") {
        return PathBuf::from(value);
    }
    if let Ok(resource_dir) = app.path().resource_dir() {
        let candidates = [
            resource_dir.join("evo-bridge.exe"),
            resource_dir.join("evo-bridge"),
            resource_dir.join("evo-bridge-x86_64-pc-windows-msvc.exe"),
            resource_dir.join("binaries/evo-bridge.exe"),
            resource_dir.join("binaries/evo-bridge"),
            resource_dir.join("binaries/evo-bridge-x86_64-pc-windows-msvc.exe"),
            resource_dir.join("usr/bin/evo-bridge"),
            resource_dir.join("evo_desktop_bridge.py"),
            resource_dir.join("bridge/evo_desktop_bridge.py"),
            resource_dir.join("_up_/bridge/evo_desktop_bridge.py"),
            resource_dir.join("_up_/_up_/bridge/evo_desktop_bridge.py"),
            resource_dir.join("_up_/_up_/config/personal_profile.example.json"),
        ];
        if let Some(candidate) = candidates.into_iter().find(|candidate| candidate.exists()) {
            return candidate;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../bridge/evo_desktop_bridge.py")
}

fn call_bridge(app: &AppHandle, state: &AppState, request: Value) -> Result<Value, String> {
    let bridge = if state.bridge.exists() {
        state.bridge.clone()
    } else {
        resolve_bridge(app)
    };
    let request_line = format!(
        "{}\n",
        serde_json::to_string(&request).map_err(|error| error.to_string())?
    );
    let mut command = if bridge.extension().and_then(|item| item.to_str()) == Some("py") {
        let python = env::var("EVO_PYTHON").unwrap_or_else(|_| {
            if cfg!(windows) {
                "python".into()
            } else {
                "python3".into()
            }
        });
        let mut process = Command::new(python);
        process.arg(&bridge);
        if let Some(repo) = bridge
            .parent()
            .and_then(Path::parent)
            .and_then(Path::parent)
        {
            process.env("PYTHONPATH", repo);
        }
        process
    } else {
        Command::new(&bridge)
    };
    let mut child = command
        .arg("--workspace")
        .arg(&state.workspace)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("could not start local Evo bridge: {error}"))?;
    use std::io::Write;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| "bridge stdin unavailable".to_string())?
        .write_all(request_line.as_bytes())
        .map_err(|error| error.to_string())?;
    let output = child
        .wait_with_output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let envelope: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("invalid bridge response: {error}"))?;
    if envelope.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(envelope
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("local Evo bridge rejected the request")
            .to_string());
    }
    Ok(envelope.get("value").cloned().unwrap_or(Value::Null))
}

#[tauri::command]
fn get_profile(app: AppHandle, state: State<'_, AppState>) -> Result<Value, String> {
    call_bridge(&app, &state, json!({"command": "get_profile"}))
}

#[tauri::command]
fn get_status(app: AppHandle, state: State<'_, AppState>) -> Result<Value, String> {
    call_bridge(&app, &state, json!({"command": "get_status"}))
}

#[tauri::command]
fn list_tasks(
    app: AppHandle,
    state: State<'_, AppState>,
    limit: Option<u32>,
) -> Result<Value, String> {
    call_bridge(
        &app,
        &state,
        json!({"command": "list_tasks", "limit": limit.unwrap_or(30)}),
    )
}

#[tauri::command]
fn submit_goal(
    app: AppHandle,
    state: State<'_, AppState>,
    goal: String,
    approval_required: bool,
) -> Result<Value, String> {
    call_bridge(
        &app,
        &state,
        json!({"command": "submit_goal", "goal": goal, "approvalRequired": approval_required}),
    )
}

#[tauri::command]
fn run_cycle(app: AppHandle, state: State<'_, AppState>) -> Result<Value, String> {
    call_bridge(&app, &state, json!({"command": "run_cycle"}))
}

#[tauri::command]
fn set_safe_mode(
    app: AppHandle,
    state: State<'_, AppState>,
    enabled: bool,
    reason: String,
) -> Result<Value, String> {
    call_bridge(
        &app,
        &state,
        json!({"command": "set_safe_mode", "enabled": enabled, "reason": reason}),
    )
}

#[tauri::command]
fn kill_switch(
    app: AppHandle,
    state: State<'_, AppState>,
    reason: String,
) -> Result<Value, String> {
    call_bridge(
        &app,
        &state,
        json!({"command": "kill_switch", "reason": reason}),
    )
}

#[tauri::command]
fn approve_task(
    app: AppHandle,
    state: State<'_, AppState>,
    task_id: String,
    scope_hash: String,
    reason: String,
) -> Result<Value, String> {
    call_bridge(
        &app,
        &state,
        json!({"command": "approve_task", "taskId": task_id, "scopeHash": scope_hash, "reason": reason}),
    )
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let workspace = default_workspace(app.handle());
            std::fs::create_dir_all(&workspace).expect("Evo workspace cannot be created");
            app.manage(AppState {
                workspace,
                bridge: resolve_bridge(app.handle()),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_profile,
            get_status,
            list_tasks,
            submit_goal,
            run_cycle,
            set_safe_mode,
            kill_switch,
            approve_task
        ])
        .run(tauri::generate_context!())
        .expect("error while running Evo desktop application");
}
