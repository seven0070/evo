from __future__ import annotations

import argparse
import json
from pathlib import Path

from .kernel import AgentKernel
from .model_adapter import OpenAICompatibleAdapter, RuleBasedAdapter
from .models import ToolCall


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evo", description="Run the local-first permissioned AI agent")
    parser.add_argument("request", nargs="?", help="Goal for the agent")
    parser.add_argument("--workspace", default="./workspace", help="Allowlisted workspace directory")
    parser.add_argument("--model", default="offline", help="offline or an OpenAI-compatible model ID")
    parser.add_argument("--base-url", default=None, help="Optional OpenAI-compatible API base URL")
    parser.add_argument("--json", action="store_true", help="Print structured JSON output")
    return parser


def approval_prompt(call: ToolCall, reason: str) -> bool:
    answer = input(f"Approval required: {reason}. Tool={call.tool_name}, args={call.arguments}. Approve? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def main() -> int:
    args = build_parser().parse_args()
    if not args.request:
        print("Provide a goal, for example: evo --workspace ./workspace 'list the files'")
        return 2
    if args.model == "offline":
        adapter = RuleBasedAdapter()
    else:
        adapter = OpenAICompatibleAdapter(args.model, args.base_url)
    kernel = AgentKernel(Path(args.workspace), adapter, approval_callback=approval_prompt)
    outcome = kernel.run(args.request)
    if args.json:
        print(json.dumps(outcome.to_dict(), indent=2))
    else:
        print(f"[{outcome.status.value}] {outcome.summary}")
        print(f"Task ID: {outcome.task_id}")
        if outcome.error:
            print(f"Error: {outcome.error}")
    return 0 if outcome.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
