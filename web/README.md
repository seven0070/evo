# Evo Command Surface

This directory contains a standalone **React + Vite** web interface for the Evo Agent. It is a JARVIS-inspired, browser-based command surface designed around explicit human control, observable status, and reviewable interactions.

> The current application is an interface prototype. It provides local command handling, optional browser speech recognition, and speech synthesis. It does **not** connect to the Evo kernel, execute external actions, access credentials, or control devices.

## Run locally

```bash
cd web
pnpm install --frozen-lockfile
pnpm dev
```

To produce a production build, run:

```bash
pnpm build
```

## Included capabilities

| Area | Current behavior |
| --- | --- |
| Command entry | Typed commands produce contextual, browser-local assistant responses. |
| Voice input | Uses the browser Speech Recognition API when available and permitted. |
| Spoken response | Uses the browser Speech Synthesis API when available. |
| Safety language | Clearly distinguishes interface simulations from real external action. |
| Visual system | Uses a dark graphite instrument panel, Arc Amber status signals, custom imagery, and responsive layouts. |

## Integration direction

The repository’s existing Evo kernel remains the authority for permissions, planning, execution, verification, and approvals. A future integration should introduce a narrow, authenticated local bridge that forwards a reviewed command to the kernel and returns structured status. The web interface must not bypass the kernel’s approval or verification boundaries.
