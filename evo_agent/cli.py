from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark import BenchmarkEngine
from .capability import CapabilityIntelligence, CapabilityRequirement, Provenance as CapabilityProvenance, ProvenanceSource as CapabilityProvenanceSource
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
from .security import SecurityPolicy
from .tools import ToolRegistry
from .storage import SQLiteStore
from .world import EnvironmentObserver, WorldModelEngine, WorldRefreshEngine
from .runtime import AgentRuntime, RuntimeSchedule, ScheduleKind, TaskPriority, TaskSource
from .external import ExternalAccessPolicy, ExternalIntegrationManager, ExternalOperationRisk, integration_operation_from_row
from .specialist import SpecialistDelegationEngine, SpecialistRisk


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
    parser.add_argument("--show-capability", metavar="CAPABILITY_ID", help="Show one rich Phase 12 capability")
    parser.add_argument("--find-capability", metavar="QUERY", help="Find rich capabilities by name or description")
    parser.add_argument("--list-tools", action="store_true", help="List rich Phase 12 tool descriptors")
    parser.add_argument("--show-tool", metavar="TOOL_ID", help="Show one rich Phase 12 tool descriptor")
    parser.add_argument("--find-tools", metavar="QUERY", help="Find rich tools by name, description, or capability")
    parser.add_argument("--analyze-capability-gap", metavar="CAPABILITY", help="Analyze availability of one capability requirement")
    parser.add_argument("--analyze-tool-selection", metavar="GOAL", help="Analyze deterministic capability/tool selection for a goal")
    parser.add_argument("--capability-stats", action="store_true", help="Show Phase 12 capability and tool statistics")
    parser.add_argument("--tool-health", action="store_true", help="Show Phase 12 tool health records")
    parser.add_argument("--show-environment", action="store_true", help="Observe and show the bounded current environment")
    parser.add_argument("--environment-snapshot", action="store_true", help="Create or show the latest immutable environment snapshot")
    parser.add_argument("--environment-diff", action="store_true", help="Compare the two latest environment snapshots")
    parser.add_argument("--before-snapshot", default="", help="Older snapshot ID used with --environment-diff")
    parser.add_argument("--after-snapshot", default="", help="Newer snapshot ID used with --environment-diff")
    parser.add_argument("--show-world-state", action="store_true", help="Show task-bounded world state")
    parser.add_argument("--show-observations", action="store_true", help="Show persisted world observations")
    parser.add_argument("--show-environment-changes", action="store_true", help="Show persisted environment differences")
    parser.add_argument("--refresh-environment", nargs="?", const="environment", default=None, metavar="KIND", help="Refresh a bounded environment subject")
    parser.add_argument("--environment-stats", action="store_true", help="Show environment and world statistics")
    parser.add_argument("--runtime-start", action="store_true", help="Start or recover the persistent runtime")
    parser.add_argument("--runtime-stop", action="store_true", help="Gracefully stop the persistent runtime")
    parser.add_argument("--runtime-kill-switch", action="store_true", help="Activate the independent emergency stop")
    parser.add_argument("--runtime-status", action="store_true", help="Show persistent runtime status")
    parser.add_argument("--runtime-pause", action="store_true", help="Pause the persistent runtime")
    parser.add_argument("--runtime-resume", action="store_true", help="Resume after environment revalidation")
    parser.add_argument("--runtime-safe-mode", action="store_true", help="Enable safe mode")
    parser.add_argument("--runtime-cancel-task", metavar="TASK_ID", help="Cancel a queued or waiting runtime task")
    parser.add_argument("--runtime-pause-task", metavar="TASK_ID", help="Pause a runtime task")
    parser.add_argument("--runtime-resume-task", metavar="TASK_ID", help="Resume a paused runtime task")
    parser.add_argument("--runtime-list-tasks", action="store_true", help="List persistent runtime tasks")
    parser.add_argument("--runtime-show-task", metavar="TASK_ID", help="Show one persistent runtime task")
    parser.add_argument("--runtime-submit", metavar="GOAL", help="Submit one bounded goal to the persistent runtime queue")
    parser.add_argument("--runtime-priority", choices=[item.value for item in TaskPriority], default="normal", help="Priority for --runtime-submit")
    parser.add_argument("--runtime-approval", action="store_true", help="Require exact human approval before runtime execution")
    parser.add_argument("--runtime-cycle", action="store_true", help="Run one bounded persistent-runtime cycle")
    parser.add_argument("--runtime-heartbeat", action="store_true", help="Record and show one runtime heartbeat")
    parser.add_argument("--runtime-health", action="store_true", help="Show runtime health")
    parser.add_argument("--list-integrations", action="store_true", help="List registered external integrations")
    parser.add_argument("--show-integration", metavar="INTEGRATION_ID", help="Show one external integration")
    parser.add_argument("--external-health", "--integration-health", dest="external_health", metavar="INTEGRATION_ID", help="Show connector health for an external integration")
    parser.add_argument("--test-integration", dest="test_integration", metavar="INTEGRATION_ID", help="Run a bounded connector availability test")
    parser.add_argument("--external-policy", action="store_true", help="Show the latest persisted external access policy")
    parser.add_argument("--list-external-policies", action="store_true", help="List persisted external access policies")
    parser.add_argument("--show-external-policy", dest="show_external_policy", metavar="POLICY_ID", help="Show one persisted external access policy")
    parser.add_argument("--list-external-operations", action="store_true", help="List persisted external operations")
    parser.add_argument("--show-external-operation", metavar="OPERATION_ID", help="Show one external operation")
    parser.add_argument("--external-submit", metavar="INTEGRATION_ID", help="Request one controlled external operation")
    parser.add_argument("--external-operation", default="read", help="External operation name used with --external-submit")
    parser.add_argument("--external-target", default="", help="External resource or endpoint used with --external-submit")
    parser.add_argument("--external-payload", default="{}", help="JSON payload used with --external-submit")
    parser.add_argument("--external-risk", choices=[item.value for item in ExternalOperationRisk], default=None, help="Optional external risk override")
    parser.add_argument("--external-enqueue", metavar="OPERATION_ID", help="Queue a persisted external operation in the bounded runtime")
    parser.add_argument("--approve-external-operation", metavar="OPERATION_ID", help="Record an explicit human approval for an external operation")
    parser.add_argument("--external-approval-scope", default="", help="Exact approval scope hash for --approve-external-operation")
    parser.add_argument("--external-approval-actor", default="human", help="Human actor recorded for external approval")
    parser.add_argument("--external-approval-reason", default="", help="Reason recorded for external approval")
    parser.add_argument("--list-integration-capabilities", action="store_true", help="List registered external integration capabilities")
    parser.add_argument("--list-external-observations", "--show-external-observations", dest="list_external_observations", action="store_true", help="List bounded external observations")
    parser.add_argument("--external-diff", action="store_true", help="Compare two external observations")
    parser.add_argument("--before-external-observation", default="", help="Older observation ID used with --external-diff")
    parser.add_argument("--after-external-observation", default="", help="Newer observation ID used with --external-diff")
    parser.add_argument("--list-external-changes", action="store_true", help="List external resource changes")
    parser.add_argument("--external-stats", action="store_true", help="Show external integration statistics")
    parser.add_argument("--list-specialists", action="store_true", help="List registered specialist roles")
    parser.add_argument("--show-specialist", metavar="SPECIALIST_ID", help="Show one specialist role")
    parser.add_argument("--specialist-health", nargs="?", const="", default=None, metavar="SPECIALIST_ID", help="Show specialist health, optionally for one specialist")
    parser.add_argument("--specialist-stats", action="store_true", help="Show specialist and delegation statistics")
    parser.add_argument("--specialist-task", metavar="GOAL", help="Create a bounded specialist task contract")
    parser.add_argument("--specialist-id", default="specialist_analysis", help="Specialist role used with --specialist-task")
    parser.add_argument("--specialist-parent-task", default="cli-parent", help="Parent task ID for a specialist contract")
    parser.add_argument("--specialist-scope", default="", help="Explicit specialist contract scope")
    parser.add_argument("--specialist-risk", choices=[item.value for item in SpecialistRisk], default=SpecialistRisk.READ_ONLY.value, help="Risk class for --specialist-task")
    parser.add_argument("--queue-specialist-task", metavar="SPECIALIST_TASK_ID", help="Queue a persisted specialist task in the bounded runtime")
    parser.add_argument("--delegate-task", dest="delegate_task", metavar="SPECIALIST_TASK_ID", help="Delegate a persisted specialist task through the bounded runtime")
    parser.add_argument("--cancel-specialist-task", metavar="SPECIALIST_TASK_ID", help="Cancel a specialist task")
    parser.add_argument("--list-specialist-tasks", action="store_true", help="List persisted specialist tasks")
    parser.add_argument("--show-specialist-task", metavar="SPECIALIST_TASK_ID", help="Show one specialist task")
    parser.add_argument("--list-delegations", action="store_true", help="List persisted delegation runs")
    parser.add_argument("--show-delegation", metavar="DELEGATION_ID", help="Show one delegation run")
    parser.add_argument("--list-specialist-evidence", action="store_true", help="List persisted specialist evidence")
    parser.add_argument("--show-specialist-evidence", dest="show_specialist_evidence", metavar="EVIDENCE_ID", help="Show one specialist evidence record")
    parser.add_argument("--list-specialist-conflicts", action="store_true", help="List specialist evidence conflicts")
    parser.add_argument("--show-conflicts", dest="show_conflicts", action="store_true", help="Show specialist evidence conflicts")
    return parser


def approval_prompt(call: ToolCall, reason: str) -> bool:
    answer = input(f"Approval required: {reason}. Tool={call.tool_name}, args={call.arguments}. Approve? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


def inspect_command(args: argparse.Namespace) -> bool:
    if not (args.list_experiences or args.show_experience or args.show_evaluation or args.analyze_evolution or args.list_proposals or args.show_proposal or args.approve_proposal or args.reject_proposal or args.list_experiments or args.show_experiment or args.sandbox_proposal or args.list_benchmarks or args.run_benchmark or args.show_evidence or args.list_versions or args.show_version or args.request_promotion or args.approve_promotion or args.reject_promotion or args.promote or args.rollback or args.list_components or args.list_capabilities or args.show_architecture or args.analyze_metamorphosis or args.list_metamorphosis or args.show_metamorphosis or args.approve_metamorphosis or args.list_opportunities or args.show_opportunity or args.list_work_items or args.show_work_item or args.list_approval_requests or args.approve_orchestration or args.run_orchestrator or args.resume_work_item or args.run_goal or args.show_goal or args.show_plan or args.show_task or args.show_cognitive_state or args.clarify_goal or args.list_memory or args.show_memory or args.search_memory or args.memory_history or args.memory_provenance or args.list_procedures or args.show_procedure or args.memory_stats or args.memory_integrity or args.archive_memory or args.restore_memory or args.delete_user_memory or args.show_capability or args.find_capability or args.list_tools or args.show_tool or args.find_tools or args.analyze_capability_gap or args.analyze_tool_selection or args.capability_stats or args.tool_health or args.show_environment or args.environment_snapshot or args.environment_diff or args.show_world_state or args.show_observations or args.show_environment_changes or args.refresh_environment is not None or args.environment_stats or args.runtime_start or args.runtime_stop or args.runtime_kill_switch or args.runtime_status or args.runtime_pause or args.runtime_resume or args.runtime_safe_mode or args.runtime_cancel_task or args.runtime_pause_task or args.runtime_resume_task or args.runtime_list_tasks or args.runtime_show_task or args.runtime_submit or args.runtime_cycle or args.runtime_heartbeat or args.runtime_health or args.list_integrations or args.show_integration or args.external_health or args.test_integration or args.external_policy or args.list_external_policies or args.show_external_policy or args.list_integration_capabilities or args.list_external_operations or args.show_external_operation or args.external_submit or args.external_enqueue or args.approve_external_operation or args.list_external_observations or args.external_diff or args.list_external_changes or args.external_stats or args.list_specialists or args.show_specialist or args.specialist_health is not None or args.specialist_stats or args.specialist_task or args.queue_specialist_task or args.delegate_task or args.cancel_specialist_task or args.list_specialist_tasks or args.show_specialist_task or args.list_delegations or args.show_delegation or args.list_specialist_evidence or args.show_specialist_evidence or args.list_specialist_conflicts or args.show_conflicts):
        return False
    workspace = Path(args.workspace).expanduser().resolve()
    store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
    memory = MemoryManager(store, workspace)
    capability_intelligence = None
    if args.show_capability or args.find_capability or args.list_tools or args.show_tool or args.find_tools or args.analyze_capability_gap or args.analyze_tool_selection or args.capability_stats or args.tool_health or args.list_capabilities or args.show_environment or args.environment_snapshot or args.environment_diff or args.show_world_state or args.show_observations or args.show_environment_changes or args.refresh_environment is not None or args.environment_stats:
        policy = SecurityPolicy(workspace)
        capability_intelligence = CapabilityIntelligence(store, workspace, ToolRegistry(policy), policy, memory)
    world = None
    if args.show_environment or args.environment_snapshot or args.environment_diff or args.show_world_state or args.show_observations or args.show_environment_changes or args.refresh_environment is not None or args.environment_stats:
        world_policy = SecurityPolicy(workspace)
        if capability_intelligence is None:
            capability_intelligence = CapabilityIntelligence(store, workspace, ToolRegistry(world_policy), world_policy, memory)
        observer = EnvironmentObserver(workspace, store, capability_intelligence, world_policy)
        world = WorldModelEngine(store, observer, WorldRefreshEngine(observer, store))
    experiences = ExperienceEngine(store)
    evolver = Evolver(store, experiences)
    metamorphosis = MetamorphosisEngine(store, Path(args.source_root)) if (args.list_components or args.list_capabilities or args.show_architecture or args.analyze_metamorphosis or args.list_metamorphosis or args.show_metamorphosis or args.approve_metamorphosis or args.list_opportunities or args.show_opportunity or args.list_work_items or args.show_work_item or args.list_approval_requests or args.approve_orchestration or args.run_orchestrator or args.resume_work_item) else None
    orchestrator = EvolutionOrchestrator(store, Path(args.source_root), policy=OrchestrationPolicy()) if metamorphosis else None
    external_manager = None
    if args.list_integrations or args.show_integration or args.external_health or args.test_integration or args.external_policy or args.list_external_policies or args.show_external_policy or args.list_integration_capabilities or args.list_external_operations or args.show_external_operation or args.external_submit or args.external_enqueue or args.approve_external_operation or args.list_external_observations or args.external_diff or args.list_external_changes or args.external_stats:
        external_manager = ExternalIntegrationManager(workspace, store=store, memory=memory)
    specialist_manager = None
    if args.list_specialists or args.show_specialist or args.specialist_health is not None or args.specialist_stats or args.specialist_task or args.queue_specialist_task or args.delegate_task or args.cancel_specialist_task or args.list_specialist_tasks or args.show_specialist_task or args.list_delegations or args.show_delegation or args.list_specialist_evidence or args.show_specialist_evidence or args.list_specialist_conflicts or args.show_conflicts or args.run_goal:
        specialist_manager = SpecialistDelegationEngine(store, workspace, memory=memory, capability_intelligence=capability_intelligence, external_integrations=external_manager)
    runtime = None
    if args.runtime_start or args.runtime_stop or args.runtime_kill_switch or args.runtime_status or args.runtime_pause or args.runtime_resume or args.runtime_safe_mode or args.runtime_cancel_task or args.runtime_pause_task or args.runtime_resume_task or args.runtime_list_tasks or args.runtime_show_task or args.runtime_submit or args.runtime_cycle or args.runtime_heartbeat or args.runtime_health or args.external_enqueue or args.approve_external_operation or args.queue_specialist_task or args.delegate_task or args.cancel_specialist_task:
        runtime = AgentRuntime(workspace, model=(RuleBasedAdapter() if args.model == "offline" else OpenAICompatibleAdapter(args.model, args.base_url)), store=store, source_root=Path(args.source_root), external_integrations=external_manager, specialist_delegation=specialist_manager)
    cognitive = None
    if args.run_goal or args.show_goal or args.show_plan or args.show_task or args.show_cognitive_state or args.clarify_goal:
        adapter = RuleBasedAdapter() if args.model == "offline" else OpenAICompatibleAdapter(args.model, args.base_url)
        kernel = AgentKernel(workspace, adapter, store=store, approval_callback=approval_prompt)
        cognitive = CognitiveOrchestrator(workspace, store=store, kernel=kernel, evolution_orchestrator=orchestrator, external_integrations=external_manager, specialist_delegation=specialist_manager)
    if args.runtime_start:
        print_json(runtime.start().to_dict())
    elif args.runtime_stop:
        print_json(runtime.stop().to_dict())
    elif args.runtime_kill_switch:
        print_json(runtime.kill_switch("CLI emergency stop").to_dict())
    elif args.runtime_status:
        print_json(runtime.status())
    elif args.runtime_pause:
        print_json(runtime.pause().to_dict())
    elif args.runtime_resume:
        print_json(runtime.resume().to_dict())
    elif args.runtime_safe_mode:
        print_json(runtime.set_safe_mode(True).to_dict())
    elif args.runtime_submit:
        print_json(runtime.enqueue_task(args.runtime_submit, priority=args.runtime_priority, approval_requirement=args.runtime_approval).to_dict())
    elif args.runtime_cycle:
        print_json(runtime.run_cycle().to_dict())
    elif args.runtime_cancel_task:
        print_json(runtime.cancel_task(args.runtime_cancel_task).to_dict())
    elif args.runtime_pause_task:
        print_json(runtime.pause_task(args.runtime_pause_task).to_dict())
    elif args.runtime_resume_task:
        print_json(runtime.resume_task(args.runtime_resume_task).to_dict())
    elif args.runtime_list_tasks:
        print_json([task.to_dict() for task in runtime.tasks()])
    elif args.runtime_show_task:
        task = runtime.task(args.runtime_show_task)
        print_json(task.to_dict() if task else {"error": "runtime task not found", "task_id": args.runtime_show_task})
    elif args.runtime_heartbeat:
        print_json(runtime.heartbeat.beat().to_dict())
    elif args.runtime_health:
        print_json(runtime.health().to_dict())
    elif args.list_specialists:
        print_json([item.to_dict() for item in specialist_manager.registry.list()])
    elif args.show_specialist:
        item = specialist_manager.registry.get(args.show_specialist)
        print_json(item.to_dict() if item else {"error": "specialist not found", "specialist_id": args.show_specialist})
    elif args.specialist_health is not None:
        if args.specialist_health:
            print_json(specialist_manager.registry.health(args.specialist_health).to_dict())
        else:
            print_json([{"specialist_id": item.specialist_id, "health": item.health.to_dict()} for item in specialist_manager.registry.list()])
    elif args.specialist_stats:
        print_json(specialist_manager.stats())
    elif args.specialist_task:
        task, contract = specialist_manager.create_contract(args.specialist_parent_task, args.specialist_task, args.specialist_id, scope=args.specialist_scope or None, risk=SpecialistRisk(args.specialist_risk))
        print_json({"task": task.to_dict(), "contract": contract.to_dict()})
    elif args.queue_specialist_task or args.delegate_task:
        print_json(runtime.enqueue_specialist_task(args.queue_specialist_task or args.delegate_task).to_dict())
    elif args.cancel_specialist_task:
        print_json(specialist_manager.cancel_task(args.cancel_specialist_task).to_dict())
    elif args.list_specialist_tasks:
        print_json([row for row in store.find_specialist_tasks(limit=200)])
    elif args.show_specialist_task:
        print_json(store.specialist_task_by_id(args.show_specialist_task) or {"error": "specialist task not found", "specialist_task_id": args.show_specialist_task})
    elif args.list_delegations:
        print_json(store.find_delegation_runs(limit=200))
    elif args.show_delegation:
        print_json(store.delegation_by_id(args.show_delegation) or {"error": "delegation not found", "delegation_id": args.show_delegation})
    elif args.list_specialist_evidence:
        print_json(store.find_specialist_evidence(limit=200))
    elif args.show_specialist_evidence:
        rows = store.find_specialist_evidence(limit=1000)
        print_json(next((row for row in rows if row.get("evidence_id") == args.show_specialist_evidence), {"error": "specialist evidence not found", "evidence_id": args.show_specialist_evidence}))
    elif args.list_specialist_conflicts or args.show_conflicts:
        print_json(store.find_evidence_conflicts(limit=200))
    elif args.list_integrations:
        print_json([item.to_dict() for item in external_manager.list_integrations()])
    elif args.show_integration:
        item = external_manager.get_integration(args.show_integration)
        print_json(item.to_dict() if item else {"error": "integration not found", "integration_id": args.show_integration})
    elif args.external_health:
        print_json(external_manager.health(args.external_health))
    elif args.test_integration:
        print_json(external_manager.health(args.test_integration))
    elif args.external_policy:
        print_json(external_manager.policy.to_dict())
    elif args.list_external_policies:
        print_json([item.to_dict() for item in external_manager.list_policies()])
    elif args.show_external_policy:
        item = next((item for item in external_manager.list_policies() if item.policy_id == args.show_external_policy), None)
        print_json(item.to_dict() if item else {"error": "external policy not found", "policy_id": args.show_external_policy})
    elif args.list_integration_capabilities:
        print_json(store.find_integration_capabilities())
    elif args.list_external_operations:
        print_json([json.loads(item["payload"]) for item in store.find_integration_operations()])
    elif args.show_external_operation:
        row = store.integration_operation_by_id(args.show_external_operation)
        print_json(json.loads(row["payload"]) if row else {"error": "external operation not found", "operation_id": args.show_external_operation})
    elif args.external_submit:
        try:
            payload = json.loads(args.external_payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--external-payload must be JSON: {exc}") from exc
        operation = external_manager.request_operation(args.external_submit, args.external_operation, args.external_target, payload, risk_level=args.external_risk)
        print_json(operation.to_dict())
    elif args.external_enqueue:
        print_json(runtime.enqueue_external_operation(args.external_enqueue).to_dict())
    elif args.approve_external_operation:
        row = store.integration_operation_by_id(args.approve_external_operation)
        if not row:
            print_json({"error": "external operation not found", "operation_id": args.approve_external_operation})
        else:
            operation, _ = integration_operation_from_row(row)
            scope = args.external_approval_scope or external_manager.approval_scope(operation)
            approved = external_manager.approve_operation(args.approve_external_operation, args.external_approval_actor, scope, args.external_approval_reason)
            print_json({"operation": approved.to_dict(), "resumed_tasks": [item.task_id for item in runtime.resume_external_operation(args.approve_external_operation)] if runtime else []})
    elif args.list_external_observations:
        print_json([item.to_dict() for item in external_manager.external_observations()])
    elif args.external_diff:
        if args.before_external_observation and args.after_external_observation:
            print_json(external_manager.external_diff(args.before_external_observation, args.after_external_observation).to_dict())
        else:
            observations = external_manager.external_observations(limit=2)
            if len(observations) >= 2:
                print_json(external_manager.external_diff(observations[1].observation_id, observations[0].observation_id).to_dict())
            else:
                print_json({"error": "at least two external observations are required"})
    elif args.list_external_changes:
        print_json(store.find_external_changes())
    elif args.external_stats:
        print_json({"integration_count": len(external_manager.list_integrations()), "operation_count": len(store.find_integration_operations()), "observation_count": len(external_manager.external_observations()), "change_count": len(store.find_external_changes()), "communication_count": len(store.find_communication_records()), "health_records": len(store.find_connector_health()), "policy": external_manager.policy.to_dict()})
    elif args.show_environment:
        model = world.observe("CLI bounded environment inspection")
        world.save_observations(model)
        print_json(model.environment.to_dict())
    elif args.environment_snapshot:
        model = world.observe("CLI environment snapshot")
        snapshot = world.create_snapshot(model)
        world.save_observations(model)
        print_json(snapshot.to_dict())
    elif args.environment_diff:
        snapshots = world.store.list_environment_snapshots(limit=2)
        if args.before_snapshot and args.after_snapshot:
            print_json(world.diff(args.before_snapshot, args.after_snapshot).to_dict())
        elif len(snapshots) >= 2:
            print_json(world.diff(snapshots[1].snapshot_id, snapshots[0].snapshot_id).to_dict())
        else:
            print_json({"error": "at least two valid environment snapshots are required"})
    elif args.show_world_state:
        model = world.current or world.observe("CLI world-state inspection")
        print_json(model.to_dict())
    elif args.show_observations:
        print_json([item.to_dict() for item in world.observations(limit=200)])
    elif args.show_environment_changes:
        print_json(world.changes(limit=100))
    elif args.refresh_environment is not None:
        model = world.refresh(kind=args.refresh_environment, reason="CLI requested bounded refresh", goal="CLI environment refresh")
        world.save_observations(model)
        print_json(model.to_dict())
    elif args.environment_stats:
        print_json(world.stats())
    elif args.show_capability:
        item = capability_intelligence.capabilities.get_capability(args.show_capability)
        print_json(item.to_dict() if item else {"error": "capability not found", "capability_id": args.show_capability})
    elif args.find_capability:
        print_json([item.to_dict() for item in capability_intelligence.capabilities.find_capabilities(args.find_capability)])
    elif args.list_tools:
        print_json([item.to_dict() for item in capability_intelligence.tools.list_tools()])
    elif args.show_tool:
        item = capability_intelligence.tools.get_tool(args.show_tool)
        print_json(item.to_dict() if item else {"error": "tool not found", "tool_id": args.show_tool})
    elif args.find_tools:
        print_json([item.to_dict() for item in capability_intelligence.tools.find_tools(args.find_tools)])
    elif args.analyze_capability_gap:
        capability_name = args.analyze_capability_gap.strip().lower().replace(" ", "_")
        requirement = CapabilityRequirement(f"cli_{capability_name}", capability_name, f"CLI capability-gap analysis for {args.analyze_capability_gap}", provenance=CapabilityProvenance(CapabilityProvenanceSource.SYSTEM, "cli"))
        analysis = capability_intelligence.analyze_requirement(requirement, capability_intelligence.build_context(args.analyze_capability_gap, requirements=[requirement]))
        print_json(analysis.to_dict())
    elif args.analyze_tool_selection:
        print_json([item.to_dict() for item in capability_intelligence.analyze_goal(args.analyze_tool_selection)])
    elif args.capability_stats:
        print_json(capability_intelligence.statistics())
    elif args.tool_health:
        print_json([{"tool_id": item.tool_id, "tool": item.name, "version": item.version, "status": item.status.value, "health": item.health.to_dict(), "reliability": item.reliability} for item in capability_intelligence.tools.list_tools()])
    elif args.clarify_goal:
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
        print_json([capability.to_dict() for capability in capability_intelligence.capabilities.list_capabilities()])
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
