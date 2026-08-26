import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const required = ["ui/index.html", "ui/styles.css", "ui/app.js", "src-tauri/Cargo.toml", "src-tauri/src/main.rs", "src-tauri/tauri.conf.json"];
const missing = required.filter((file) => !existsSync(join(root, file)));
if (missing.length) throw new Error(`Missing desktop files: ${missing.join(", ")}`);
const html = readFileSync(join(root, "ui/index.html"), "utf8");
const config = JSON.parse(readFileSync(join(root, "src-tauri/tauri.conf.json"), "utf8"));
const rust = readFileSync(join(root, "src-tauri/src/main.rs"), "utf8");
if (!config.bundle.externalBin?.includes("binaries/evo-bridge")) throw new Error("Tauri manifest must package the Evo bridge sidecar");
if (!config.bundle.icon?.includes("icons/icon.png")) throw new Error("Tauri manifest must declare the Evo icon");
if (config.bundle.windows?.nsis?.installerIcon !== "icons/icon.ico") throw new Error("NSIS installer must use the Evo icon");
if (String(config.app?.security?.csp || "").includes("http")) throw new Error("desktop CSP must not allow remote sources");
if (!rust.includes("app.path().app_data_dir()")) throw new Error("packaged workspace must use Tauri app-data storage");
if (!rust.includes("EVO_WORKSPACE")) throw new Error("desktop must retain the explicit workspace override");
for (const needle of ["goal-input", "approval-check", "safe-mode-button", "kill-switch-button", "history-list"]) {
  if (!html.includes(needle)) throw new Error(`UI is missing required control: ${needle}`);
}
const app = readFileSync(join(root, "ui/app.js"), "utf8");
for (const command of ["get_profile", "get_status", "list_tasks", "submit_goal", "run_cycle", "set_safe_mode", "kill_switch", "approve_task"]) {
  if (!app.includes(command)) throw new Error(`UI does not invoke required bridge command: ${command}`);
}
if (app.includes("setInterval(run_cycle")) throw new Error("UI must not auto-run Runtime cycles");
console.log(JSON.stringify({ status: "pass", required_files: required.length, bridge_commands: 8 }));
