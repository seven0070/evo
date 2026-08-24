from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark import BenchmarkEngine
from .experience import ExperienceEngine
from .evolver import Evolver
from .kernel import AgentKernel
from .model_adapter import OpenAICompatibleAdapter, RuleBasedAdapter
from .metamorphosis import MetamorphosisEngine
from .promotion import PromotionEngine
from .models import ToolCall
from .sandbox import SandboxEngine
from .storage import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evo", description="Run and inspect the local-first permissioned AI agent")
    parser.add_argument("request", nargs="?", help="Goal for the agent")
    parser.add_argument("--workspace", default="./workspace", help="Allowlisted workspace directory")
    parser.add_argument("--source-root", default=".", help="Production source root used only as a read-only sandbox baseline")
    parser.add_argument("--sandbox-root", default=None, help="Optional directory outside the production source root for experiments")
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
    parser.add_argument("--analyze-evolution", action="store_true", help="Analyze historical evidence and persist proposals")
    parser.add_argument("--list-proposals", action="store_true", help="List evolution proposals")
    parser.add_argument("--show-proposal", metavar="PROPOSAL_ID", help="Show one evolution proposal")
    parser.add_argument("--approve-proposal", metavar="PROPOSAL_ID", help="Record approval for a proposal for a future sandbox phase")
    parser.add_argument("--reject-proposal", metavar="PROPOSAL_ID", help="Record rejection for a proposal")
    parser.add_argument("--proposal-reason", default="", help="Reason recorded with proposal approval or rejection")
    parser.add_argument("--list-experiments", action="store_true", help="List sandbox experiments")
    parser.add_argument("--show-experiment", metavar="EXPERIMENT_ID", help="Show one sandbox experiment")
    parser.add_argument("--sandbox-proposal", metavar="PROPOSAL_ID", help="Run an approved proposal in an isolated sandbox")
    parser.add_argument("--retain-sandbox", action="store_true", help="Retain a passed sandbox experiment for benchmark evaluation")
    parser.add_argument("--list-benchmarks", action="store_true", help="List deterministic benchmark definitions")
    parser.add_argument("--run-benchmark", metavar="BENCHMARK_ID", help="Run a benchmark against an eligible sandbox experiment")
    parser.add_argument("--experiment", metavar="EXPERIMENT_ID", help="Sandbox experiment to benchmark")
    parser.add_argument("--show-evidence", metavar="EVIDENCE_ID", help="Show one comparative evaluation evidence package")
    parser.add_argument("--list-versions", action="store_true", help="List registered agent versions")
    parser.add_argument("--show-version", metavar="VERSION_ID", help="Show one registered agent version")
    parser.add_argument("--request-promotion", metavar="CANDIDATE_VERSION", help="Request promotion of a candidate version")
    parser.add_argument("--evidence", metavar="EVIDENCE_ID", help="Evidence used for a promotion request")
    parser.add_argument("--approve-promotion", metavar="PROMOTION_ID", help="Record explicit human promotion approval")
    parser.add_argument("--reject-promotion", metavar="PROMOTION_ID", help="Reject a promotion request")
    parser.add_argument("--promote", metavar="PROMOTION_ID", help="Activate an explicitly approved candidate")
    parser.add_argument("--rollback", metavar="VERSION_ID", help="Rollback the active version to the previous known-good version")
    parser.add_argument("--rollback-reason", default="", help="Reason recorded for rollback")
    parser.add_argument("--list-components", action="store_true", help="List registered architecture components")
    parser.add_argument("--list-capabilities", action="store_true", help="List registered architecture capabilities")
    parser.add_argument("--show-architecture", action="store_true", help="Show the current architecture manifest")
    parser.add_argument("--analyze-metamorphosis", action="store_true", help="Create and validate a governed structural proposal")
    parser.add_argument("--list-metamorphosis", action="store_true", help="List governed metamorphosis proposals and experiments")
    parser.add_argument("--show-metamorphosis", metavar="METAMORPHOSIS_ID", help="Show one governed metamorphosis proposal and experiments")
    parser.add_argument("--approve-metamorphosis", metavar="METAMORPHOSIS_ID", help="Record explicit metamorphosis approval")
    return parser


def approval_prompt(call: ToolCall, reason: str) -> bool:
    answer = input(f"Approval required: {reason}. Tool={call.tool_name}, args={call.arguments}. Approve? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


def inspect_command(args: argparse.Namespace) -> bool:
    if not (args.list_experiences or args.show_experience or args.show_evaluation or args.analyze_evolution or args.list_proposals or args.show_proposal or args.approve_proposal or args.reject_proposal or args.list_experiments or args.show_experiment or args.sandbox_proposal or args.list_benchmarks or args.run_benchmark or args.show_evidence or args.list_versions or args.show_version or args.request_promotion or args.approve_promotion or args.reject_promotion or args.promote or args.rollback or args.list_components or args.list_capabilities or args.show_architecture or args.analyze_metamorphosis or args.list_metamorphosis or args.show_metamorphosis or args.approve_metamorphosis):
        return False
    workspace = Path(args.workspace).expanduser().resolve()
    store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
    experiences = ExperienceEngine(store)
    evolver = Evolver(store, experiences)
    metamorphosis = MetamorphosisEngine(store, Path(args.source_root)) if (args.list_components or args.list_capabilities or args.show_architecture or args.analyze_metamorphosis or args.list_metamorphosis or args.show_metamorphosis or args.approve_metamorphosis) else None
    if args.list_components:
        print_json([component.to_dict() for component in metamorphosis.list_components()])
    elif args.list_capabilities:
        print_json([capability.to_dict() for capability in metamorphosis.list_capabilities()])
    elif args.show_architecture:
        print_json(metamorphosis.get_architecture().to_dict())
    elif args.analyze_metamorphosis:
        change = metamorphosis.identify_structural_opportunity(args.request or "add capability for structured context")
        proposal = metamorphosis.generate_proposal(change, "Explore a bounded structural improvement without changing protected controls")
        valid, errors = metamorphosis.validate_proposal(proposal)
        result = proposal.to_dict()
        result["valid"] = valid
        result["validation_errors"] = errors
        print_json(result)
    elif args.list_metamorphosis:
        print_json({"proposals": [proposal.to_dict() for proposal in metamorphosis.list_proposals()], "experiments": [experiment.to_dict() for experiment in metamorphosis.list_experiments()]})
    elif args.show_metamorphosis:
        proposal = metamorphosis.get_proposal(args.show_metamorphosis)
        experiments = [experiment.to_dict() for experiment in metamorphosis.list_experiments() if experiment.proposal_id == args.show_metamorphosis]
        print_json({"proposal": proposal.to_dict() if proposal else None, "experiments": experiments})
    elif args.approve_metamorphosis:
        print_json(metamorphosis.approve_proposal(args.approve_metamorphosis, args.proposal_reason).to_dict())
    elif args.list_experiences:
        records = [item.to_dict() for item in experiences.retrieve(task_type=args.task_type, outcome=args.outcome, strategy=args.strategy, tool=args.tool, limit=20)]
        print_json(records)
    elif args.show_experience:
        record = store.experience_by_id(args.show_experience)
        print_json(record or {"error": "experience not found", "experience_id": args.show_experience})
    elif args.show_evaluation:
        record = store.evaluation_by_id(args.show_evaluation)
        print_json(record or {"error": "evaluation not found", "evaluation_id": args.show_evaluation})
    elif args.analyze_evolution:
        print_json([proposal.to_dict() for proposal in evolver.analyze_and_persist()])
    elif args.list_proposals:
        print_json([proposal.to_dict() for proposal in evolver.list_proposals(limit=50)])
    elif args.show_proposal:
        proposal = evolver.get_proposal(args.show_proposal)
        print_json(proposal.to_dict() if proposal else {"error": "proposal not found", "proposal_id": args.show_proposal})
    elif args.approve_proposal:
        print_json(evolver.approve(args.approve_proposal, args.proposal_reason).to_dict())
    elif args.reject_proposal:
        print_json(evolver.reject(args.reject_proposal, args.proposal_reason).to_dict())
    elif args.list_experiments:
        sandbox = SandboxEngine(store, Path(args.source_root), Path(args.sandbox_root).expanduser().resolve() if args.sandbox_root else None)
        print_json([experiment.to_dict() for experiment in sandbox.list_experiments(limit=50)])
    elif args.show_experiment:
        sandbox = SandboxEngine(store, Path(args.source_root), Path(args.sandbox_root).expanduser().resolve() if args.sandbox_root else None)
        experiment = sandbox.get_experiment(args.show_experiment)
        print_json(experiment.to_dict() if experiment else {"error": "experiment not found", "experiment_id": args.show_experiment})
    elif args.sandbox_proposal:
        sandbox = SandboxEngine(store, Path(args.source_root), Path(args.sandbox_root).expanduser().resolve() if args.sandbox_root else None)
        print_json(sandbox.run_experiment(args.sandbox_proposal, retain_sandbox=args.retain_sandbox).to_dict())
    elif args.list_benchmarks:
        benchmark_engine = BenchmarkEngine(store, Path(args.source_root))
        if not benchmark_engine.list_benchmarks():
            benchmark_engine.save_benchmark(benchmark_engine.default_benchmark())
        print_json([benchmark.to_dict() for benchmark in benchmark_engine.list_benchmarks(limit=50)])
    elif args.run_benchmark:
        if not args.experiment:
            print_json({"error": "--experiment is required with --run-benchmark"})
        else:
            benchmark_engine = BenchmarkEngine(store, Path(args.source_root))
            if not benchmark_engine.load_benchmark(args.run_benchmark):
                benchmark_engine.save_benchmark(benchmark_engine.default_benchmark(args.run_benchmark))
            print_json(benchmark_engine.run(args.run_benchmark, args.experiment).to_dict())
    elif args.show_evidence:
        record = store.evidence_by_id(args.show_evidence)
        print_json(record or {"error": "evidence not found", "evidence_id": args.show_evidence})
    elif args.list_versions or args.show_version or args.request_promotion or args.approve_promotion or args.reject_promotion or args.promote or args.rollback:
        promotion = PromotionEngine(store, Path(args.source_root), Path(args.sandbox_root).expanduser().resolve() if args.sandbox_root else None)
        if args.list_versions:
            print_json([version.to_dict() for version in promotion.list_versions()])
        elif args.show_version:
            version = promotion.get_version(args.show_version)
            print_json(version.to_dict() if version else {"error": "version not found", "version_id": args.show_version})
        elif args.request_promotion:
            if not args.evidence:
                print_json({"error": "--evidence is required with --request-promotion"})
            else:
                print_json(promotion.request_promotion(args.request_promotion, args.evidence, "human").to_dict())
        elif args.approve_promotion:
            print_json(promotion.approve_promotion(args.approve_promotion, args.proposal_reason).to_dict())
        elif args.reject_promotion:
            print_json(promotion.reject_promotion(args.reject_promotion, args.proposal_reason).to_dict())
        elif args.promote:
            print_json(promotion.promote(args.promote).to_dict())
        elif args.rollback:
            print_json(promotion.rollback(args.rollback, args.rollback_reason).to_dict())
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
