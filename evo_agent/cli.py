from __future__ import annotations

import argparse
import json
from pathlib import Path

from .experience import ExperienceEngine
from .kernel import AgentKernel
from .model_adapter import OpenAICompatibleAdapter, RuleBasedAdapter
from .models import ToolCall
from .storage import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evo", description="Run and inspect the local-first permissioned AI agent")
    parser.add_argument("request", nargs="?", help="Goal for the agent")
    parser.add_argument("--workspace", default="./workspace", help="Allowlisted workspace directory")
    parser.add_argument("--model", default="offline", help="offline or an OpenAI-compatible model ID")
    parser.add_argument("--base-url", default=None, help="Optional OpenAI-compatible API base URL")
    parser.add_argument("--json", action="store_true", help="Print structured JSON output")
    parser.add_argument("--list-experiences", action="store_true", help="List recent structured experiences")
    parser.add_argument("--show-experience", metavar="EXPERIENCE_ID", help="Show one structured experience")
    parser.add_argument("--show-evaluation", metavar="EVALUATION_ID", help="Show one evaluation result")
    parser.add_argument("--task-type", help="Filter experiences by task type")
    parser.add_argument("--outcome", help="Filter experiences by outcome")
    parser.add_argument("--strategy", help="Filter experiences by strategy")
    parser.add_argument("--tool", help="Filter experiences by tool")
    return parser


def approval_prompt(call: ToolCall, reason: str) -> bool:
    answer = input(f"Approval required: {reason}. Tool={call.tool_name}, args={call.arguments}. Approve? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


def inspect_command(args: argparse.Namespace) -> bool:
    if not (args.list_experiences or args.show_experience or args.show_evaluation):
        return False
    store = SQLiteStore(Path(args.workspace).expanduser().resolve() / ".evo" / "agent.sqlite3")
    experiences = ExperienceEngine(store)
    if args.list_experiences:
        records = [item.to_dict() for item in experiences.retrieve(task_type=args.task_type, outcome=args.outcome, strategy=args.strategy, tool=args.tool, limit=20)]
        print_json(records)
    elif args.show_experience:
        record = store.experience_by_id(args.show_experience)
        print_json(record or {"error": "experience not found", "experience_id": args.show_experience})
    elif args.show_evaluation:
        record = store.evaluation_by_id(args.show_evaluation)
        print_json(record or {"error": "evaluation not found", "evaluation_id": args.show_evaluation})
    return True


def main() -> int:
    args = build_parser().parse_args()
    if inspect_command(args):
        return 0
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
        print_json(outcome.to_dict())
    else:
        print(f"[{outcome.status.value}] {outcome.summary}")
        print(f"Task ID: {outcome.task_id}")
        if outcome.error:
            print(f"Error: {outcome.error}")
    return 0 if outcome.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
