from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark import BenchmarkEngine
from .cognitive import CognitiveOrchestrator, CognitiveOutcome
from .experience import ExperienceEngine
from .evolver import Evolver
from .kernel import AgentKernel
from .model_adapter import OpenAICompatibleAdapter, RuleBasedAdapter
from .memory import MemoryManager, MemoryStatus, MemoryType, RetrievalQuery
from .metamorphosis import MetamorphosisEngine
from .orchestrator import ApprovalType, EvolutionOrchestrator, OrchestrationPolicy
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
    parser.add_argument("--legacy-kernel", action="store_true", help="Use the direct Phase 1 Kernel path instead of the Cognitive Layer")
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
    parser.add_argument("--list-opportunities", action="store_true", help="List detected evolution opportunities")
    parser.add_argument("--show-opportunity", metavar="OPPORTUNITY_ID", help="Show one evolution opportunity")
    parser.add_argument("--list-work-items", action="store_true", help="List persistent orchestration work items")
    parser.add_argument("--show-work-item", metavar="WORK_ITEM_ID", help="Show one orchestration work item")
    parser.add_argument("--list-approval-requests", action="store_true", help="List pending and completed orchestration approvals")
    parser.add_argument("--approve-orchestration", metavar="WORK_ITEM_ID", help="Record a human decision for an orchestration approval")
    parser.add_argument("--approval-type", choices=[item.value for item in ApprovalType], default=ApprovalType.EVOLUTION.value, help="Approval gate addressed by --approve-orchestration")
    parser.add_argument("--approval-decision", choices=["approve", "reject"], default="approve", help="Human decision for --approve-orchestration")
    parser.add_argument("--approval-actor", default="human", help="Human actor recorded with orchestration approval")
    parser.add_argument("--run-orchestrator", action="store_true", help="Run one bounded orchestration cycle")
    parser.add_argument("--resume-work-item", metavar="WORK_ITEM_ID", help="Safely resume one persisted work item")
    parser.add_argument("--run-goal", metavar="GOAL", help="Run one bounded Cognitive Layer goal lifecycle")
    parser.add_argument("--show-goal", metavar="GOAL_ID", help="Show one persisted cognitive goal")
    parser.add_argument("--show-plan", metavar="PLAN_ID", help="Show one persisted cognitive plan")
    parser.add_argument("--show-task", metavar="TASK_ID", help="Show one persisted cognitive subtask")
    parser.add_argument("--show-cognitive-state", metavar="GOAL_ID", help="Show persisted cognitive state for a goal")
    parser.add_argument("--clarify-goal", metavar="GOAL_ID", help="Resume an ambiguous goal with explicit clarification")
    parser.add_argument("--clarification", default="", help="Clarification text used with --clarify-goal")
    parser.add_argument("--list-memory", action="store_true", help="List durable and active memory records")
    parser.add_argument("--show-memory", metavar="MEMORY_ID", help="Show one memory record")
    parser.add_argument("--search-memory", metavar="QUERY", help="Search memory with deterministic bounded retrieval")
    parser.add_argument("--memory-history", metavar="MEMORY_ID", help="Show version history and supersession links")
    parser.add_argument("--memory-provenance", metavar="MEMORY_ID", help="Show memory provenance and source chain")
    parser.add_argument("--list-procedures", action="store_true", help="List procedural memories")
    parser.add_argument("--show-procedure", metavar="PROCEDURE_ID", help="Show one procedural memory")
    parser.add_argument("--memory-stats", action="store_true", help="Show memory statistics")
    parser.add_argument("--memory-integrity", action="store_true", help="Validate memory schema and record integrity")
    parser.add_argument("--archive-memory", metavar="MEMORY_ID", help="Archive a non-user memory explicitly")
    parser.add_argument("--restore-memory", metavar="MEMORY_ID", help="Restore an archived or expired memory")
    parser.add_argument("--delete-user-memory", metavar="MEMORY_ID", help="Explicitly archive user-owned memory")
    return parser


def approval_prompt(call: ToolCall, reason: str) -> bool:
    answer = input(f"Approval required: {reason}. Tool={call.tool_name}, args={call.arguments}. Approve? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


def inspect_command(args: argparse.Namespace) -> bool:
    if not (args.list_experiences or args.show_experience or args.show_evaluation or args.analyze_evolution or args.list_proposals or args.show_proposal or args.approve_proposal or args.reject_proposal or args.list_experiments or args.show_experiment or args.sandbox_proposal or args.list_benchmarks or args.run_benchmark or args.show_evidence or args.list_versions or args.show_version or args.request_promotion or args.approve_promotion or args.reject_promotion or args.promote or args.rollback or args.list_components or args.list_capabilities or args.show_architecture or args.analyze_metamorphosis or args.list_metamorphosis or args.show_metamorphosis or args.approve_metamorphosis or args.list_opportunities or args.show_opportunity or args.list_work_items or args.show_work_item or args.list_approval_requests or args.approve_orchestration or args.run_orchestrator or args.resume_work_item or args.run_goal or args.show_goal or args.show_plan or args.show_task or args.show_cognitive_state or args.clarify_goal or args.list_memory or args.show_memory or args.search_memory or args.memory_history or args.memory_provenance or args.list_procedures or args.show_procedure or args.memory_stats or args.memory_integrity or args.archive_memory or args.restore_memory or args.delete_user_memory):
        return False
    workspace = Path(args.workspace).expanduser().resolve()
    store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
    memory = MemoryManager(store, workspace)
    experiences = ExperienceEngine(store)
    evolver = Evolver(store, experiences)
    metamorphosis = MetamorphosisEngine(store, Path(args.source_root)) if (args.list_components or args.list_capabilities or args.show_architecture or args.analyze_metamorphosis or args.list_metamorphosis or args.show_metamorphosis or args.approve_metamorphosis or args.list_opportunities or args.show_opportunity or args.list_work_items or args.show_work_item or args.list_approval_requests or args.approve_orchestration or args.run_orchestrator or args.resume_work_item) else None
    orchestrator = EvolutionOrchestrator(store, Path(args.source_root), policy=OrchestrationPolicy()) if metamorphosis else None
    cognitive = None
    if args.run_goal or args.show_goal or args.show_plan or args.show_task or args.show_cognitive_state or args.clarify_goal:
        adapter = RuleBasedAdapter() if args.model == "offline" else OpenAICompatibleAdapter(args.model, args.base_url)
        kernel = AgentKernel(workspace, adapter, store=store, approval_callback=approval_prompt)
        cognitive = CognitiveOrchestrator(workspace, store=store, kernel=kernel, evolution_orchestrator=orchestrator)
    if args.clarify_goal:
        if not args.clarification:
            print_json({"error": "--clarification is required with --clarify-goal"})
        else:
            print_json(cognitive.clarify(args.clarify_goal, args.clarification).to_dict())
    elif args.run_goal:
        print_json(cognitive.run_goal(args.run_goal).to_dict())
    elif args.show_goal:
        goal = cognitive.persistence.load_goal(args.show_goal)
        print_json(goal.to_dict() if goal else {"error": "goal not found", "goal_id": args.show_goal})
    elif args.show_plan:
        row = store.cognitive_plan_by_goal(args.show_plan) or store.cognitive_plan_by_id(args.show_plan)
        print_json(row or {"error": "plan not found", "plan_id": args.show_plan})
    elif args.show_task:
        row = store.cognitive_task_by_id(args.show_task)
        print_json(row or {"error": "task not found", "task_id": args.show_task})
    elif args.show_cognitive_state:
        row = store.cognitive_state_by_goal(args.show_cognitive_state)
        print_json(row or {"error": "cognitive state not found", "goal_id": args.show_cognitive_state})
    elif args.list_memory:
        memory_type = MemoryType(args.task_type) if args.task_type in {item.value for item in MemoryType} else None
        print_json([item.to_dict() for item in memory.list(memory_type=memory_type)])
    elif args.show_memory:
        item = memory.get(args.show_memory)
        print_json(item.to_dict() if item else {"error": "memory not found", "memory_id": args.show_memory})
    elif args.search_memory:
        print_json([item.to_dict() for item in memory.retrieve(RetrievalQuery(goal=args.search_memory, max_memories=10, max_memory_bytes=12000))])
    elif args.memory_history:
        print_json([item.to_dict() for item in memory.get_history(args.memory_history)])
    elif args.memory_provenance:
        try:
            print_json(memory.get_provenance(args.memory_provenance))
        except KeyError:
            print_json({"error": "memory not found", "memory_id": args.memory_provenance})
    elif args.list_procedures:
        print_json([item.to_dict() for item in memory.list_procedures()])
    elif args.show_procedure:
        procedure = next((item for item in memory.list_procedures() if item.procedure_id == args.show_procedure), None)
        print_json(procedure.to_dict() if procedure else {"error": "procedure not found", "procedure_id": args.show_procedure})
    elif args.memory_stats:
        print_json(memory.statistics())
    elif args.memory_integrity:
        print_json(memory.validate_integrity().to_dict())
    elif args.archive_memory:
        print_json(memory.archive(args.archive_memory).to_dict())
    elif args.restore_memory:
        print_json(memory.restore(args.restore_memory).to_dict())
    elif args.delete_user_memory:
        print_json(memory.delete_user_memory(args.delete_user_memory, actor="cli").to_dict())
    elif args.list_opportunities:
        print_json([opportunity.to_dict() for opportunity in orchestrator.list_opportunities()])
    elif args.show_opportunity:
        opportunity = orchestrator.get_opportunity(args.show_opportunity)
        print_json(opportunity.to_dict() if opportunity else {"error": "opportunity not found", "opportunity_id": args.show_opportunity})
    elif args.list_work_items:
        print_json([item.to_dict() for item in orchestrator.list_work_items()])
    elif args.show_work_item:
        item = orchestrator.get_work_item(args.show_work_item)
        payload = {"work_item": item.to_dict() if item else None, "events": store.find_orchestration_events(args.show_work_item)}
        print_json(payload)
    elif args.list_approval_requests:
        print_json([request.to_dict() for request in orchestrator.list_approval_requests()])
    elif args.approve_orchestration:
        decision = args.approval_decision == "approve"
        print_json(orchestrator.manage_approval(args.approve_orchestration, args.approval_type, decision, args.proposal_reason, actor=args.approval_actor).to_dict())
    elif args.run_orchestrator:
        print_json(orchestrator.run_cycle().to_dict())
    elif args.resume_work_item:
        recovered = orchestrator.resume(args.resume_work_item)
        print_json(recovered.to_dict() if recovered else {"error": "work item not found", "work_item_id": args.resume_work_item})
    elif args.list_components:
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
    workspace = Path(args.workspace).expanduser().resolve()
    store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
    kernel = AgentKernel(workspace, adapter, store=store, approval_callback=approval_prompt)
    if args.legacy_kernel:
        outcome = kernel.run(args.request)
        if args.json:
            print_json(outcome.to_dict())
        else:
            print(f"[{outcome.status.value}] {outcome.summary}")
            print(f"Task ID: {outcome.task_id}")
            if outcome.error:
                print(f"Error: {outcome.error}")
        return 0 if outcome.status.value == "succeeded" else 1
    cognitive = CognitiveOrchestrator(workspace, store=store, kernel=kernel)
    result = cognitive.run_goal(args.request)
    if args.json:
        print_json(result.to_dict())
    else:
        print(f"[{result.outcome.value}] {result.summary}")
        print(f"Goal ID: {result.goal.goal_id}")
        if result.state.last_error:
            print(f"Error: {result.state.last_error}")
    return 0 if result.outcome is CognitiveOutcome.SUCCESS else 1


if __name__ == "__main__":
    raise SystemExit(main())
