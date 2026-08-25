# Evo V1 configuration

Evo is local-first and provider-neutral. Configuration is supplied through CLI arguments and the selected workspace; there is no required hosted service, background daemon, connector, or credential store for offline operation.

| Setting | Default | Meaning |
|---|---|---|
| `--workspace` | `./workspace` | The only allowlisted data workspace. SQLite state and checkpoints are stored below this directory. |
| `--source-root` | `.` | Read-only production source baseline used by sandbox, architecture, and integrity checks. |
| `--sandbox-root` | automatic bounded location | Optional location for isolated candidate experiments outside the production source root. |
| `--model` | `offline` | Deterministic local adapter. A provider-specific model is opt-in and remains behind the existing adapter, policy, timeout, and verification boundaries. |
| `--base-url` | unset | Optional OpenAI-compatible endpoint used only when a non-offline model is explicitly selected. |
| `--json` | disabled | Emits bounded structured output for scripts and operational inspection. |

## State layout

A workspace contains `.evo/agent.sqlite3` for persistent records and `.evo/checkpoints/` for Kernel checkpoints. Do not edit the database while Evo is running. The Runtime validates SQLite integrity and persisted JSON payloads during startup and fails closed when corruption is detected.

The source root is not a writable agent workspace. Evolution and Metamorphosis use isolated candidates, structured configuration or manifests, fixed tests, and existing approval, benchmark, promotion, health-check, and rollback authorities. They do not rewrite arbitrary source files.

## Safety configuration

Filesystem paths must resolve inside the workspace. Shell commands must begin with an allowlisted executable and restricted operations are rejected. Medium-, high-, and critical-risk actions require explicit approval. External integrations are default-deny and use their own operation policy, credential isolation, freshness, schema, rate, and approval controls. Strategic, learning, self-model, specialist, and model-intelligence records are advisory evidence and cannot grant permissions or override Governance, Runtime, Kernel, or Verification.

For reproducible offline development, use a clean virtual environment and install only the package and selected development extras:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

An optional provider adapter can be installed separately with `python -m pip install -e '.[llm]'`. Do not place API keys in workspaces, goals, prompts, memory, logs, or persisted strategic records.
