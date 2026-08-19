"""Command-line entry point for TAREL."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from tarel import __version__
from tarel.annotations.contracts import AnnotationFailure, AnnotationTask
from tarel.annotations.review import AnnotationReviewRecord
from tarel.application import (
    add_knowledge_document_use_case,
    add_relationship_use_case,
    add_workspace_relationship_use_case,
    apply_annotation_use_case,
    build_focus_use_case,
    build_graph_use_case,
    build_retrieval_index_use_case,
    check_connector_use_case,
    check_provider_use_case,
    check_relationship_use_case,
    compile_context_prefix_use_case,
    compile_context_use_case,
    compile_workspace_context_prefix_use_case,
    compile_workspace_context_use_case,
    configure_provider_use_case,
    context_packet_impact_use_case,
    create_demo_use_case,
    create_workspace_use_case,
    decide_annotation_use_case,
    decide_relationship_use_case,
    decide_workspace_relationship_use_case,
    define_workspace_area_use_case,
    define_workspace_system_use_case,
    define_workspace_zone_use_case,
    diff_context_packets_use_case,
    discover_catalog_use_case,
    discover_relationships_use_case,
    download_embedding_model_use_case,
    edit_annotation_use_case,
    embedding_model_status_use_case,
    list_annotation_reviews_use_case,
    list_focuses_use_case,
    list_graphs_use_case,
    list_knowledge_documents_use_case,
    list_provider_names_use_case,
    list_relationships_use_case,
    list_workspaces_use_case,
    load_focus_use_case,
    load_graph_use_case,
    load_knowledge_document_use_case,
    load_workspace_use_case,
    plan_annotations_use_case,
    plan_focus_annotations_use_case,
    probe_connector_use_case,
    profile_connector_use_case,
    refresh_graph_use_case,
    resolve_knowledge_use_case,
    resolve_workspace_scope_use_case,
    retrieval_index_status_use_case,
    run_annotation_batch_use_case,
    sample_connector_use_case,
    scaffold_connector_use_case,
    scaffold_provider_use_case,
    search_graph_use_case,
    search_workspace_use_case,
    show_annotation_use_case,
    show_workspace_zone_use_case,
    test_provider_use_case,
)
from tarel.connectors.contracts import (
    CatalogResult,
    ConnectorCheck,
    ConnectorFailure,
    ObjectProfileResult,
    ProbeResult,
    RelationshipPairProfile,
    SampleResult,
)
from tarel.context import DEFAULT_MAX_CONTEXT_CHARACTERS, ContextFailure, ContextResult
from tarel.demo import DemoFailure
from tarel.focus.contracts import FocusDocument, FocusFailure
from tarel.graph.contracts import GraphDocument, GraphFailure
from tarel.grounding import GroundingBundle
from tarel.grounding_application import (
    compile_graph_grounding_use_case,
    compile_workspace_grounding_use_case,
)
from tarel.knowledge.contracts import (
    DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    KnowledgeFailure,
)
from tarel.lineage.cli import add_lineage_commands, dispatch_lineage
from tarel.lineage.contracts import LineageFailure
from tarel.providers.config import BUILTIN_PROVIDER_ADAPTERS
from tarel.providers.contracts import ProviderCheck, ProviderFailure
from tarel.relationships.core import RelationshipFailure
from tarel.retrieval.contracts import RetrievalFailure
from tarel.retrieval.local import DEFAULT_MODEL_NAME
from tarel.search import SearchFailure, SearchResults
from tarel.semantics.cli import add_semantic_commands, dispatch_semantic
from tarel.semantics.contracts import SemanticFailure
from tarel.sources.application import (
    SourceCheck,
    SourceEnrichmentResult,
    build_source_graph_use_case,
    check_source_use_case,
    configure_source_use_case,
    discover_source_use_case,
    enrich_source_use_case,
    list_sources_use_case,
    load_source_use_case,
    probe_source_use_case,
    refresh_source_graph_use_case,
)
from tarel.sources.contracts import SourceFailure, SourceProfile
from tarel.workspaces.contracts import WorkspaceDocument, WorkspaceFailure
from tarel.workspaces.core import ResolvedZone
from tarel.workspaces.scope import ResolvedScope


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tarel",
        description="Compile trusted analytics context for coding agents.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("version", help="Print the installed TAREL version.")

    demo = subcommands.add_parser(
        "demo",
        help="Create deterministic local sources for connector and graph walkthroughs.",
    )
    demo_commands = demo.add_subparsers(dest="demo_command")
    demo_create = demo_commands.add_parser(
        "create",
        help="Create a local demo source and its private connector configuration.",
    )
    demo_create.add_argument("name", choices=("retail-dwh",))
    demo_create.add_argument("--path", type=Path, help="SQLite target path.")
    demo_create.add_argument("--version", type=int, choices=(1, 2), default=1)
    demo_create.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing demo database and configuration.",
    )
    _add_format_argument(demo_create)

    workspace = subcommands.add_parser(
        "workspace",
        help="Organize graphs as systems, areas, schemas, and overlapping zones.",
    )
    workspace_commands = workspace.add_subparsers(dest="workspace_command")

    workspace_create = workspace_commands.add_parser(
        "create",
        help="Create an empty local workspace.",
    )
    workspace_create.add_argument("name")
    workspace_create.add_argument("--description")
    _add_format_argument(workspace_create)

    workspace_list = workspace_commands.add_parser("list", help="List local workspaces.")
    _add_format_argument(workspace_list)

    workspace_show = workspace_commands.add_parser("show", help="Show one workspace hierarchy.")
    workspace_show.add_argument("name")
    _add_format_argument(workspace_show)

    workspace_scope = workspace_commands.add_parser(
        "scope",
        help="Resolve systems, areas, schemas, graphs, and zones to graph objects.",
    )
    workspace_scope.add_argument("name", help="Workspace name.")
    _add_scope_arguments(workspace_scope)
    _add_format_argument(workspace_scope)

    workspace_system = workspace_commands.add_parser(
        "system",
        help="Define logical systems over one or more graphs.",
    )
    workspace_system_commands = workspace_system.add_subparsers(dest="workspace_system_command")
    workspace_system_define = workspace_system_commands.add_parser(
        "define",
        help="Create or replace a system graph assignment.",
    )
    workspace_system_define.add_argument("workspace_name")
    workspace_system_define.add_argument("system_name")
    workspace_system_define.add_argument("--graph", action="append", required=True, dest="graphs")
    workspace_system_define.add_argument("--description")
    _add_format_argument(workspace_system_define)

    workspace_area = workspace_commands.add_parser(
        "area",
        help="Group sibling schemas inside a system.",
    )
    workspace_area_commands = workspace_area.add_subparsers(dest="workspace_area_command")
    workspace_area_define = workspace_area_commands.add_parser(
        "define",
        help="Create or replace an area schema assignment.",
    )
    workspace_area_define.add_argument("workspace_name")
    workspace_area_define.add_argument("system_name")
    workspace_area_define.add_argument("area_name")
    workspace_area_define.add_argument(
        "--schema",
        action="append",
        required=True,
        dest="schemas",
        help="Schema reference as GRAPH:NAMESPACE.",
    )
    workspace_area_define.add_argument("--description")
    _add_format_argument(workspace_area_define)

    workspace_zone = workspace_commands.add_parser(
        "zone",
        help="Define overlapping object sets across schemas and areas.",
    )
    workspace_zone_commands = workspace_zone.add_subparsers(dest="workspace_zone_command")
    workspace_zone_define = workspace_zone_commands.add_parser(
        "define",
        help="Create or replace an explicit zone membership.",
    )
    workspace_zone_define.add_argument("workspace_name")
    workspace_zone_define.add_argument("system_name")
    workspace_zone_define.add_argument("zone_name")
    workspace_zone_define.add_argument(
        "--object",
        action="append",
        required=True,
        dest="objects",
        help="Object reference as GRAPH:NAMESPACE.OBJECT.",
    )
    workspace_zone_define.add_argument("--description")
    _add_format_argument(workspace_zone_define)

    workspace_zone_show = workspace_zone_commands.add_parser(
        "show",
        help="Resolve a zone to current graph objects and their areas.",
    )
    workspace_zone_show.add_argument("workspace_name")
    workspace_zone_show.add_argument("system_name")
    workspace_zone_show.add_argument("zone_name")
    _add_format_argument(workspace_zone_show)

    workspace_relationship = workspace_commands.add_parser(
        "relationship",
        help="Manage explicit joins whose endpoints may belong to different graphs.",
    )
    workspace_relationship_commands = workspace_relationship.add_subparsers(
        dest="workspace_relationship_command"
    )
    workspace_relationship_add = workspace_relationship_commands.add_parser(
        "add",
        help="Add a human-authored cross-graph relationship candidate.",
    )
    workspace_relationship_add.add_argument("workspace_name")
    workspace_relationship_add.add_argument(
        "--from",
        required=True,
        dest="source_reference",
        help="Source field as GRAPH:NAMESPACE.OBJECT.FIELD.",
    )
    workspace_relationship_add.add_argument(
        "--to",
        required=True,
        dest="target_reference",
        help="Target field as GRAPH:NAMESPACE.OBJECT.FIELD.",
    )
    workspace_relationship_add.add_argument("--reason", required=True)
    workspace_relationship_add.add_argument(
        "--validated",
        action="store_true",
        help="Persist as human-validated instead of draft.",
    )
    _add_format_argument(workspace_relationship_add)

    workspace_relationship_list = workspace_relationship_commands.add_parser(
        "list",
        help="List persisted workspace relationships.",
    )
    workspace_relationship_list.add_argument("workspace_name")
    _add_format_argument(workspace_relationship_list)

    for decision in ("validate", "reject"):
        workspace_relationship_decide = workspace_relationship_commands.add_parser(
            decision,
            help=f"{decision.title()} a workspace relationship.",
        )
        workspace_relationship_decide.add_argument("workspace_name")
        workspace_relationship_decide.add_argument("relationship_id")
        workspace_relationship_decide.add_argument("--reason", required=True)
        _add_format_argument(workspace_relationship_decide)

    source = subcommands.add_parser(
        "source",
        help="Manage private logical sources and their connector-backed graphs.",
    )
    source_commands = source.add_subparsers(dest="source_command")
    source_configure = source_commands.add_parser(
        "configure",
        help="Create or explicitly replace a non-secret logical source profile.",
    )
    source_configure.add_argument("name")
    source_configure.add_argument("--connector", required=True)
    source_configure.add_argument(
        "--config-ref",
        dest="config_reference",
        help="env:VARIABLE or state:relative/path.toml; never a connection URL.",
    )
    source_configure.add_argument("--database")
    source_configure.add_argument("--namespace", "--schema", dest="namespace")
    source_configure.add_argument("--graph", action="append", dest="graphs")
    source_configure.add_argument(
        "--allow-aggregates",
        action="store_true",
        help="Permit bounded aggregate column profiles for this source.",
    )
    source_configure.add_argument(
        "--allow-small-domains",
        action="store_true",
        help="Permit complete small-domain counts; requires aggregate permission.",
    )
    source_configure.add_argument(
        "--allow-raw-samples",
        action="store_true",
        help="Permit ephemeral raw samples of at most ten rows per object.",
    )
    source_configure.add_argument("--replace", action="store_true")
    _add_format_argument(source_configure)

    source_list = source_commands.add_parser("list", help="List configured logical sources.")
    _add_format_argument(source_list)

    source_show = source_commands.add_parser("show", help="Show one logical source profile.")
    source_show.add_argument("name")
    _add_format_argument(source_show)

    source_check = source_commands.add_parser(
        "check",
        help="Check connector availability and whether the config reference resolves.",
    )
    source_check.add_argument("name")
    _add_format_argument(source_check)

    source_probe = source_commands.add_parser(
        "probe",
        help="Resolve the private config reference and run a bounded read-only probe.",
    )
    source_probe.add_argument("name")
    source_probe.add_argument("--database")
    _add_format_argument(source_probe)

    source_discover = source_commands.add_parser(
        "discover",
        help="Discover source metadata through the configured connector.",
    )
    source_discover.add_argument("name")
    source_discover.add_argument("--database")
    source_discover.add_argument("--namespace", "--schema", dest="namespace")
    _add_format_argument(source_discover)

    source_build = source_commands.add_parser(
        "build",
        help="Build a graph and bind it to this logical source.",
    )
    source_build.add_argument("name")
    source_build.add_argument("graph_name")
    source_build.add_argument("--database")
    source_build.add_argument("--namespace", "--schema", dest="namespace")
    _add_format_argument(source_build)

    source_refresh = source_commands.add_parser(
        "refresh",
        help="Refresh a bound graph through this logical source.",
    )
    source_refresh.add_argument("name")
    source_refresh.add_argument("graph_name")
    source_refresh.add_argument("--namespace", "--schema", dest="namespace")
    _add_format_argument(source_refresh)

    source_enrich = source_commands.add_parser(
        "enrich",
        help="Profile every graph object under the source enrichment policy.",
    )
    source_enrich.add_argument("name")
    source_enrich.add_argument("graph_name")
    source_enrich.add_argument("--profile-row-limit", type=int, default=10_000)
    source_enrich.add_argument(
        "--sample-limit",
        type=int,
        default=10,
        help="Raw rows per object when allowed; maximum 10 (default: 10).",
    )
    source_enrich.add_argument(
        "--persist-join-candidates",
        action="store_true",
        help="Persist aggregate transformed-join evidence, never sample rows.",
    )
    _add_format_argument(source_enrich)

    connector = subcommands.add_parser("connector", help="Inspect and run source connectors.")
    connector_commands = connector.add_subparsers(dest="connector_command")

    check = connector_commands.add_parser(
        "check",
        help="Validate a connector and its dependencies.",
    )
    check.add_argument("name", help="Connector name, for example sqlserver.")
    _add_format_argument(check)

    probe = connector_commands.add_parser("probe", help="Run a bounded read-only connection probe.")
    probe.add_argument("name", help="Connector name, for example sqlserver.")
    probe.add_argument("--config", type=Path, help="Private TOML configuration file.")
    probe.add_argument("--database", help="Override the configured database.")
    _add_format_argument(probe)

    discover = connector_commands.add_parser(
        "discover",
        help="Read schemas, tables, views, and fields from a source.",
    )
    discover.add_argument("name", help="Connector name, for example sqlserver.")
    discover.add_argument("--config", type=Path, help="Private TOML configuration file.")
    discover.add_argument("--database", help="Override the configured database.")
    discover.add_argument(
        "--namespace",
        "--schema",
        dest="namespace",
        help="Limit discovery to one namespace or database schema.",
    )
    _add_format_argument(discover)

    scaffold = connector_commands.add_parser(
        "scaffold",
        help="Create an isolated connector candidate for an agent or human.",
    )
    scaffold.add_argument("name", help="New connector name, for example postgres.")
    scaffold.add_argument("--output", type=Path, help="Target directory (default: connector name).")

    sample = connector_commands.add_parser(
        "sample",
        help="Explicitly read a bounded sample from one object.",
    )
    sample.add_argument("name", help="Connector name, for example sqlserver.")
    sample.add_argument("--config", type=Path, required=True, help="Private TOML configuration.")
    sample.add_argument("--database", help="Override the configured database.")
    sample.add_argument("--namespace", "--schema", dest="namespace", required=True)
    sample.add_argument("--object", dest="object_name", required=True)
    sample.add_argument("--limit", type=int, default=3)
    _add_format_argument(sample)

    profile = connector_commands.add_parser(
        "profile",
        help="Read bounded aggregate column profiles from one object.",
    )
    profile.add_argument("name", help="Connector name, for example sqlserver.")
    profile.add_argument("--config", type=Path, required=True, help="Private TOML configuration.")
    profile.add_argument("--database", help="Override the configured database.")
    profile.add_argument("--namespace", "--schema", dest="namespace", required=True)
    profile.add_argument("--object", dest="object_name", required=True)
    profile.add_argument("--row-limit", type=int, default=10_000)
    profile.add_argument("--small-domain-limit", type=int, default=20)
    profile.add_argument(
        "--include-values",
        action="store_true",
        help="Explicitly allow values for complete small domains in this ephemeral result.",
    )
    _add_format_argument(profile)

    knowledge = subcommands.add_parser(
        "knowledge",
        help="Attach bounded Markdown or text references to annotation scopes.",
    )
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command")

    knowledge_add = knowledge_commands.add_parser(
        "add",
        help="Import one local reference document into private TAREL state.",
    )
    knowledge_add.add_argument("id")
    knowledge_add.add_argument("path", type=Path)
    knowledge_add.add_argument(
        "--scope",
        required=True,
        help=(
            "global, system:NAME, graph:NAME, schema:GRAPH:NAMESPACE, "
            "or object:GRAPH:OBJECT"
        ),
    )
    knowledge_add.add_argument("--title")
    knowledge_add.add_argument("--state", choices=("draft", "validated"), default="draft")
    knowledge_add.add_argument("--workspace", help="Validate a system scope.")
    knowledge_add.add_argument("--replace", action="store_true")
    _add_format_argument(knowledge_add)

    knowledge_list = knowledge_commands.add_parser(
        "list",
        help="List registered knowledge without printing its content.",
    )
    _add_format_argument(knowledge_list)

    knowledge_show = knowledge_commands.add_parser(
        "show",
        help="Show one registered document and its content.",
    )
    knowledge_show.add_argument("id")
    _add_format_argument(knowledge_show)

    knowledge_resolve = knowledge_commands.add_parser(
        "resolve",
        help="Preview the exact bounded knowledge context for one object.",
    )
    knowledge_resolve.add_argument("graph")
    knowledge_resolve.add_argument("object")
    knowledge_resolve.add_argument("--workspace")
    knowledge_resolve.add_argument("--mode", choices=("none", "scoped"), default="scoped")
    knowledge_resolve.add_argument("--document", action="append", dest="documents")
    knowledge_resolve.add_argument(
        "--max-characters",
        type=int,
        default=DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    )
    _add_format_argument(knowledge_resolve)

    provider = subcommands.add_parser("provider", help="Configure optional annotation providers.")
    provider_commands = provider.add_subparsers(dest="provider_command")

    provider_list = provider_commands.add_parser("list", help="List supported providers.")
    _add_format_argument(provider_list)

    provider_check = provider_commands.add_parser(
        "check",
        help="Show redacted provider configuration status.",
    )
    provider_check.add_argument("name")
    _add_format_argument(provider_check)

    provider_configure = provider_commands.add_parser(
        "configure",
        help="Store provider configuration in the private user config.",
    )
    provider_configure.add_argument("name")
    provider_configure.add_argument(
        "--adapter",
        help=(
            "Adapter: openrouter, openai-compatible, or an installed tarel.providers entry point."
        ),
    )
    provider_credentials = provider_configure.add_mutually_exclusive_group()
    provider_credentials.add_argument(
        "--from-env",
        action="store_true",
        help="Read the API key from a supported provider environment variable.",
    )
    provider_credentials.add_argument(
        "--no-api-key",
        action="store_true",
        help="Configure an unauthenticated endpoint, normally a loopback server.",
    )
    provider_configure.add_argument("--model", help="Default provider model.")
    provider_configure.add_argument("--base-url", help="Provider API base URL.")
    provider_configure.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
        help="Default reasoning mode for this provider profile.",
    )
    provider_configure.add_argument(
        "--structured-mode",
        choices=("json_schema", "tool"),
        help="Structured-output mechanism for OpenAI-compatible endpoints.",
    )

    provider_test = provider_commands.add_parser(
        "test",
        help="Make one small structured-generation provider request.",
    )
    provider_test.add_argument("name")
    provider_test.add_argument("--timeout", type=float, default=120.0)
    _add_format_argument(provider_test)

    provider_scaffold = provider_commands.add_parser(
        "scaffold",
        help="Create an isolated provider-adapter candidate for review.",
    )
    provider_scaffold.add_argument("name")
    provider_scaffold.add_argument(
        "--output",
        type=Path,
        help="Target directory (default: .tarel/providers/NAME).",
    )

    model = subcommands.add_parser("model", help="Manage optional local embedding models.")
    model_commands = model.add_subparsers(dest="model_command")

    model_download = model_commands.add_parser(
        "download",
        help="Download and verify the recommended local embedding model.",
    )
    model_download.add_argument("--name", default=DEFAULT_MODEL_NAME)
    model_download.add_argument("--target", type=Path)
    model_download.add_argument("--force", action="store_true")
    _add_format_argument(model_download)

    model_status = model_commands.add_parser(
        "status",
        help="Check the recommended model path and checksum.",
    )
    model_status.add_argument("--name", default=DEFAULT_MODEL_NAME)
    model_status.add_argument("--model", type=Path, dest="model_path")
    _add_format_argument(model_status)

    index = subcommands.add_parser("index", help="Build and inspect local retrieval indexes.")
    index_commands = index.add_subparsers(dest="index_command")

    index_build = index_commands.add_parser(
        "build",
        help="Embed safe graph metadata into a rebuildable local SQLite index.",
    )
    index_build.add_argument("name", help="Local graph name.")
    index_build.add_argument("--model", type=Path, dest="model_path")
    index_build.add_argument("--batch-size", type=int, default=16)
    index_build.add_argument("--threads", type=int, dest="n_threads")
    _add_format_argument(index_build)

    index_status = index_commands.add_parser("status", help="Inspect one retrieval index.")
    index_status.add_argument("name", help="Local graph name.")
    _add_format_argument(index_status)

    graph = subcommands.add_parser("graph", help="Build and inspect local TAREL graphs.")
    graph_commands = graph.add_subparsers(dest="graph_command")

    graph_build = graph_commands.add_parser(
        "build",
        help="Discover a source and persist its technical graph.",
    )
    graph_build.add_argument("name", help="Local graph name.")
    graph_build.add_argument("--connector", required=True, help="Source connector name.")
    graph_build.add_argument("--config", type=Path, help="Private connector configuration.")
    graph_build.add_argument("--database", help="Override the configured database.")
    graph_build.add_argument("--namespace", "--schema", dest="namespace")
    _add_format_argument(graph_build)

    graph_refresh = graph_commands.add_parser(
        "refresh",
        help="Rediscover technical structure while preserving reviewed knowledge.",
    )
    graph_refresh.add_argument("name", help="Existing local graph name.")
    graph_refresh.add_argument("--config", type=Path, help="Private connector configuration.")
    graph_refresh.add_argument("--namespace", "--schema", dest="namespace")
    _add_format_argument(graph_refresh)

    graph_list = graph_commands.add_parser("list", help="List local graphs.")
    _add_format_argument(graph_list)

    graph_show = graph_commands.add_parser("show", help="Show one local graph.")
    graph_show.add_argument("name")
    _add_format_argument(graph_show)

    ui = subcommands.add_parser(
        "ui",
        help="Open the optional local graph, lineage, and annotation browser.",
    )
    ui.add_argument("graph", nargs="?", help="Single local graph to inspect.")
    ui.add_argument("--workspace", help="Open a workspace across one or more graphs.")
    _add_scope_arguments(ui)
    ui.add_argument(
        "--lineage",
        action="append",
        dest="lineages",
        help="Lineage document to include; repeat for multiple documents.",
    )
    ui.add_argument(
        "--focus",
        action="append",
        dest="focuses",
        help="Open one saved report or cube focus; repeat to combine focuses.",
    )
    ui.add_argument(
        "--edit",
        action="store_true",
        help="Allow explicit annotation, lineage, relationship, and zone changes.",
    )
    ui.add_argument("--port", type=int, default=0, help="Loopback port; 0 selects a free port.")
    ui.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")

    add_lineage_commands(subcommands)
    add_semantic_commands(subcommands)

    focus = subcommands.add_parser(
        "focus",
        help="Build reproducible report-to-source slices for demand-driven work.",
    )
    focus_commands = focus.add_subparsers(dest="focus_command")
    focus_build = focus_commands.add_parser(
        "build",
        help="Trace one seed upstream through explicitly selected sources.",
    )
    focus_build.add_argument("name")
    focus_build.add_argument("--seed", required=True)
    focus_build.add_argument("--lineage", action="append", dest="lineages")
    focus_build.add_argument("--graph", action="append", dest="graphs")
    focus_build.add_argument("--max-hops", type=int, default=12)
    focus_build.add_argument(
        "--state",
        action="append",
        choices=("draft", "review_required", "validated"),
    )
    _add_format_argument(focus_build)

    focus_show = focus_commands.add_parser("show", help="Show one persisted focus snapshot.")
    focus_show.add_argument("name")
    _add_format_argument(focus_show)

    focus_list = focus_commands.add_parser("list", help="List persisted focus snapshots.")
    _add_format_argument(focus_list)

    search = subcommands.add_parser(
        "search",
        help="Search graph metadata with lexical, BM25, vector, or hybrid retrieval.",
    )
    search.add_argument("name", help="Graph name, or workspace name with --workspace.")
    search.add_argument("query", help="Words describing the required analytical objects.")
    search.add_argument("--namespace", "--schema", dest="namespace")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument(
        "--mode",
        choices=("lexical", "bm25", "vector", "hybrid"),
        default="lexical",
    )
    search.add_argument("--model", type=Path, dest="model_path")
    search.add_argument("--threads", type=int, dest="n_threads")
    _add_workspace_retrieval_scope_arguments(search)
    _add_annotation_state_arguments(search)
    _add_format_argument(search)

    context = subcommands.add_parser(
        "context",
        help="Build and compare deterministic context packets.",
    )
    context_commands = context.add_subparsers(dest="context_command")
    context_build = context_commands.add_parser(
        "build",
        help="Compile bounded context from search and reviewed graph relationships.",
    )
    context_build.add_argument(
        "name",
        help="Graph name, or workspace name with --workspace.",
    )
    context_build.add_argument("query", help="Analytical question or compact search query.")
    context_build.add_argument("--namespace", "--schema", dest="namespace")
    context_build.add_argument("--seed-limit", type=int, default=3)
    context_build.add_argument("--max-objects", type=int, default=10)
    context_build.add_argument("--max-joins", type=int, default=12)
    context_build.add_argument("--max-hops", type=int, default=2)
    context_build.add_argument("--max-fields-per-object", type=int, default=12)
    context_build.add_argument(
        "--max-characters",
        type=int,
        default=DEFAULT_MAX_CONTEXT_CHARACTERS,
        help="Maximum canonical characters in the complete context payload.",
    )
    context_build.add_argument(
        "--mode",
        choices=("lexical", "bm25", "vector", "hybrid"),
        default="lexical",
    )
    context_build.add_argument("--model", type=Path, dest="model_path")
    context_build.add_argument("--threads", type=int, dest="n_threads")
    _add_workspace_retrieval_scope_arguments(context_build)
    _add_annotation_state_arguments(context_build)
    _add_format_argument(context_build)

    context_prefix = context_commands.add_parser(
        "prefix",
        help="Compile a query-independent graph or workspace scope for prompt caching.",
    )
    context_prefix.add_argument(
        "name",
        help="Graph name, or workspace name with --workspace.",
    )
    context_prefix.add_argument("--namespace", "--schema", dest="namespace")
    context_prefix.add_argument("--max-objects", type=int, default=250)
    context_prefix.add_argument("--max-joins", type=int, default=500)
    context_prefix.add_argument("--max-fields-per-object", type=int, default=50)
    context_prefix.add_argument("--max-characters", type=int, default=500_000)
    _add_workspace_retrieval_scope_arguments(context_prefix)
    _add_annotation_state_arguments(context_prefix)
    _add_format_argument(context_prefix)

    context_diff = context_commands.add_parser(
        "diff",
        help="Validate and compare two serialized context packets.",
    )
    context_diff.add_argument("left", type=Path)
    context_diff.add_argument("right", type=Path)
    _add_format_argument(context_diff)

    context_impact = context_commands.add_parser(
        "impact",
        help="Check whether one saved context packet is affected by the latest graph refresh.",
    )
    context_impact.add_argument("packet", type=Path)
    context_impact.add_argument("--graph", required=True, help="Current local graph name.")
    _add_format_argument(context_impact)

    grounding = subcommands.add_parser(
        "grounding",
        help="Compile agent-ready context with source, dialect, and optional lineage identity.",
    )
    grounding.add_argument("name", help="Graph name, or workspace name with --workspace.")
    grounding.add_argument("query", help="Analytical question or compact search query.")
    grounding.add_argument("--namespace", "--schema", dest="namespace")
    grounding.add_argument("--lineage", action="append", dest="lineages")
    grounding.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Select a logical execution source; repeat for multi-graph context.",
    )
    grounding.add_argument("--trace", dest="trace_reference")
    grounding.add_argument("--lineage-limit", type=int, default=8)
    grounding.add_argument(
        "--lineage-mode",
        choices=("lexical", "bm25", "vector", "hybrid"),
        default="bm25",
    )
    grounding.add_argument(
        "--lineage-state",
        action="append",
        dest="lineage_states",
        choices=("draft", "review_required", "validated"),
    )
    grounding.add_argument("--seed-limit", type=int, default=3)
    grounding.add_argument("--max-objects", type=int, default=10)
    grounding.add_argument("--max-joins", type=int, default=12)
    grounding.add_argument("--max-hops", type=int, default=2)
    grounding.add_argument("--max-trace-hops", type=int, default=12)
    grounding.add_argument("--max-fields-per-object", type=int, default=12)
    grounding.add_argument(
        "--max-characters",
        type=int,
        default=DEFAULT_MAX_CONTEXT_CHARACTERS,
    )
    grounding.add_argument(
        "--mode",
        choices=("lexical", "bm25", "vector", "hybrid"),
        default="lexical",
    )
    grounding.add_argument("--model", type=Path, dest="model_path")
    grounding.add_argument("--threads", type=int, dest="n_threads")
    _add_workspace_retrieval_scope_arguments(grounding)
    _add_annotation_state_arguments(grounding)
    _add_format_argument(grounding)

    graph_annotate = graph_commands.add_parser(
        "annotate",
        help="Run provider-backed annotation tasks, optionally in parallel.",
    )
    graph_annotate.add_argument("name")
    graph_annotate.add_argument("--provider", required=True)
    graph_annotate.add_argument("--namespace", "--schema", dest="namespace")
    graph_annotate.add_argument("--object", action="append", dest="objects")
    graph_annotate.add_argument("--limit", type=int)
    graph_annotate.add_argument("--workers", type=int, default=1)
    graph_annotate.add_argument("--retry", type=int, default=0)
    graph_annotate.add_argument("--retry-backoff", type=float, default=2.0)
    graph_annotate.add_argument("--skip-errors", action="store_true")
    graph_annotate.add_argument("--max-errors", type=int)
    graph_annotate.add_argument("--model")
    graph_annotate.add_argument("--timeout", type=float, default=120.0)
    graph_annotate.add_argument("--samples", type=int, default=0)
    graph_annotate.add_argument("--profile-rows", type=int, default=0)
    graph_annotate.add_argument(
        "--include-small-domain-values",
        action="store_true",
        help="Explicitly allow small-domain values in ephemeral annotation input.",
    )
    graph_annotate.add_argument("--config", type=Path, help="Private connector configuration.")
    graph_annotate.add_argument("--include-annotated", action="store_true")
    graph_annotate.add_argument("--dry-run", action="store_true")
    _add_annotation_knowledge_arguments(graph_annotate)
    _add_format_argument(graph_annotate)

    annotation = subcommands.add_parser(
        "annotation",
        help="Exchange annotation tasks with the current coding agent.",
    )
    annotation_commands = annotation.add_subparsers(dest="annotation_command")

    annotation_plan = annotation_commands.add_parser(
        "plan",
        help="List annotation tasks without calling an API provider.",
    )
    annotation_plan.add_argument("name", nargs="?", help="Graph name.")
    annotation_plan.add_argument("--focus", help="Plan only objects reached by this focus.")
    annotation_plan.add_argument("--namespace", "--schema", dest="namespace")
    annotation_plan.add_argument("--object", action="append", dest="objects")
    annotation_plan.add_argument("--limit", type=int)
    annotation_plan.add_argument("--include-annotated", action="store_true")
    _add_annotation_knowledge_arguments(annotation_plan)
    _add_format_argument(annotation_plan)

    annotation_next = annotation_commands.add_parser(
        "next",
        help="Return the next full annotation task as JSON for the coding agent.",
    )
    annotation_next.add_argument("name", nargs="?", help="Graph name.")
    annotation_next.add_argument("--focus", help="Return the next task inside this focus.")
    annotation_next.add_argument("--namespace", "--schema", dest="namespace")
    annotation_next.add_argument("--object", action="append", dest="objects")
    annotation_next.add_argument("--include-annotated", action="store_true")
    annotation_next.add_argument("--samples", type=int, default=0)
    annotation_next.add_argument("--profile-rows", type=int, default=0)
    annotation_next.add_argument(
        "--include-small-domain-values",
        action="store_true",
        help="Explicitly allow small-domain values in ephemeral annotation input.",
    )
    annotation_next.add_argument("--config", type=Path, help="Private connector configuration.")
    _add_annotation_knowledge_arguments(annotation_next)

    annotation_apply = annotation_commands.add_parser(
        "apply",
        help="Validate and apply one coding-agent proposal as a draft.",
    )
    annotation_apply.add_argument("name")
    annotation_apply.add_argument(
        "--input",
        required=True,
        help="Proposal JSON file or '-' for stdin.",
    )

    annotation_list = annotation_commands.add_parser(
        "list",
        help="List object and field annotation proposals for review.",
    )
    annotation_list.add_argument("name")
    annotation_list.add_argument(
        "--state",
        action="append",
        choices=("draft", "validated", "rejected", "deferred", "review_required"),
        help="Limit results to one or more review states.",
    )
    _add_format_argument(annotation_list)

    annotation_show = annotation_commands.add_parser(
        "show",
        help="Show one object or field annotation and its review history.",
    )
    annotation_show.add_argument("name")
    annotation_show.add_argument("target", help="Object, field, or stable node ID.")
    _add_format_argument(annotation_show)

    annotation_edit = annotation_commands.add_parser(
        "edit",
        help="Apply a bounded JSON patch to one annotation proposal.",
    )
    annotation_edit.add_argument("name")
    annotation_edit.add_argument("target", help="Object, field, or stable node ID.")
    annotation_edit.add_argument("--input", required=True, help="Patch JSON file or '-' for stdin.")
    annotation_edit.add_argument("--reason", required=True)
    _add_format_argument(annotation_edit)

    for decision, state_label in (
        ("validate", "validated"),
        ("reject", "rejected"),
        ("defer", "deferred"),
    ):
        annotation_decision = annotation_commands.add_parser(
            decision,
            help=f"Mark one annotation as {state_label} by a human.",
        )
        annotation_decision.add_argument("name")
        annotation_decision.add_argument("target", help="Object, field, or stable node ID.")
        annotation_decision.add_argument("--reason", required=True)
        annotation_decision.add_argument(
            "--include-fields",
            action="store_true",
            help="Apply the decision to the selected table or view and all of its fields.",
        )
        _add_format_argument(annotation_decision)

    relationship = subcommands.add_parser(
        "relationship",
        help="Add or inspect possible joins without treating them as foreign keys.",
    )
    relationship_commands = relationship.add_subparsers(dest="relationship_command")

    relationship_add = relationship_commands.add_parser(
        "add",
        help="Add a human-defined relationship candidate.",
    )
    relationship_add.add_argument("name", help="Local graph name.")
    relationship_add.add_argument("--from", dest="from_reference", required=True)
    relationship_add.add_argument("--to", dest="to_reference", required=True)
    relationship_add.add_argument("--reason", required=True)
    relationship_add.add_argument("--validated", action="store_true")
    _add_format_argument(relationship_add)

    relationship_check = relationship_commands.add_parser(
        "check",
        help="Run one bounded aggregate probe for a selected field pair.",
    )
    relationship_check.add_argument("name", help="Local graph name.")
    relationship_check.add_argument("--from", dest="from_reference", required=True)
    relationship_check.add_argument("--to", dest="to_reference", required=True)
    relationship_check.add_argument("--config", type=Path, required=True)
    relationship_check.add_argument("--row-limit", type=int, default=10_000)
    _add_format_argument(relationship_check)

    relationship_discover = relationship_commands.add_parser(
        "discover",
        help="Search a bounded neighborhood around one object for possible joins.",
    )
    relationship_discover.add_argument("name", help="Local graph name.")
    relationship_discover.add_argument("--object", dest="object_reference", required=True)
    relationship_discover.add_argument("--field", dest="field_name")
    relationship_discover.add_argument("--config", type=Path, required=True)
    relationship_discover.add_argument("--max-pairs", type=int, default=20)
    relationship_discover.add_argument("--row-limit", type=int, default=10_000)
    relationship_discover.add_argument("--min-source-coverage", type=float, default=0.85)
    relationship_discover.add_argument("--min-overlap-count", type=int, default=3)
    relationship_discover.add_argument("--min-target-uniqueness", type=float, default=0.9)
    relationship_discover.add_argument("--dry-run", action="store_true")
    relationship_discover.add_argument(
        "--focus",
        help="Restrict candidate pairs to objects reached by this focus.",
    )
    relationship_discover.add_argument(
        "--expand-one-hop",
        action="store_true",
        help="Also include objects connected by one observed foreign-key hop.",
    )
    _add_format_argument(relationship_discover)

    relationship_list = relationship_commands.add_parser(
        "list",
        help="List stored human and inferred relationship candidates.",
    )
    relationship_list.add_argument("name", help="Local graph name.")
    _add_format_argument(relationship_list)

    for decision, state_label in (("validate", "validated"), ("reject", "rejected")):
        relationship_decision = relationship_commands.add_parser(
            decision,
            help=f"Mark one inferred relationship as {state_label} by a human.",
        )
        relationship_decision.add_argument("name", help="Local graph name.")
        relationship_decision.add_argument("edge_id", help="Relationship candidate ID.")
        relationship_decision.add_argument("--reason", required=True)
        _add_format_argument(relationship_decision)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) >= 2
        and arguments[0] == "context"
        and arguments[1] not in {"build", "prefix", "diff", "impact", "-h", "--help"}
    ):
        arguments.insert(1, "build")
    args = parser.parse_args(arguments)

    try:
        lineage_result = dispatch_lineage(args)
        if lineage_result is not None:
            return lineage_result

        semantic_result = dispatch_semantic(args)
        if semantic_result is not None:
            return semantic_result

        if args.command == "focus" and args.focus_command == "build":
            result = build_focus_use_case(
                args.name,
                seed=args.seed,
                lineage_names=tuple(args.lineages or ()),
                graph_names=tuple(args.graphs or ()),
                max_hops=args.max_hops,
                states=frozenset(args.state) if args.state else None,
            )
            _render_focus(result.focus, output_format=args.format, path=result.path)
            return 0

        if args.command == "focus" and args.focus_command == "show":
            _render_focus(load_focus_use_case(args.name), output_format=args.format)
            return 0

        if args.command == "focus" and args.focus_command == "list":
            names = list_focuses_use_case()
            if args.format == "json":
                print(json.dumps({"focuses": list(names)}, indent=2, sort_keys=True))
            else:
                for name in names:
                    print(name)
            return 0

        if args.command == "version":
            print(__version__)
            return 0

        if args.command == "ui":
            from tarel.ui.server import UIFailure, run_ui

            try:
                return run_ui(
                    args.graph,
                    workspace=args.workspace,
                    systems=tuple(args.systems or ()),
                    graphs=tuple(args.graphs or ()),
                    areas=tuple(args.areas or ()),
                    schemas=tuple(args.schemas or ()),
                    zones=tuple(args.zones or ()),
                    lineages=tuple(args.lineages or ()),
                    focuses=tuple(args.focuses or ()),
                    editable=args.edit,
                    port=args.port,
                    open_browser=not args.no_open,
                )
            except UIFailure as exc:
                print(f"error [{exc.code}]: {exc}", file=sys.stderr)
                return 2

        if args.command == "demo" and args.demo_command == "create":
            result = create_demo_use_case(
                args.name,
                path=args.path,
                version=args.version,
                force=args.force,
            )
            if args.format == "json":
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            else:
                print(f"Created demo: {result.name} v{result.version}")
                print(f"Database: {result.database_path}")
                print(f"Connector config: {result.config_path}")
                print(f"Next: tarel connector probe sqlite --config {result.config_path}")
            return 0

        if args.command == "source" and args.source_command == "configure":
            result = configure_source_use_case(
                args.name,
                connector=args.connector,
                config_reference=args.config_reference,
                database=args.database,
                namespace=args.namespace,
                graphs=tuple(args.graphs or ()),
                enrichment_permissions=tuple(
                    permission
                    for permission, allowed in (
                        ("aggregates", args.allow_aggregates),
                        ("small_domains", args.allow_small_domains),
                        ("raw_samples", args.allow_raw_samples),
                    )
                    if allowed
                ),
                replace=args.replace,
            )
            _render_source(result.source, output_format=args.format)
            return 0

        if args.command == "source" and args.source_command == "list":
            names = list_sources_use_case()
            if args.format == "json":
                print(json.dumps({"sources": list(names)}, indent=2, sort_keys=True))
            else:
                for name in names:
                    print(name)
            return 0

        if args.command == "source" and args.source_command == "show":
            _render_source(load_source_use_case(args.name), output_format=args.format)
            return 0

        if args.command == "source" and args.source_command == "check":
            result = check_source_use_case(args.name)
            _render_source_check(result, output_format=args.format)
            return 0 if result.available else 1

        if args.command == "source" and args.source_command == "probe":
            result = probe_source_use_case(args.name, database=args.database)
            _render_probe(result, output_format=args.format)
            return 0

        if args.command == "source" and args.source_command == "discover":
            result = discover_source_use_case(
                args.name,
                database=args.database,
                namespace=args.namespace,
            )
            _render_catalog(result, output_format=args.format)
            return 0

        if args.command == "source" and args.source_command == "build":
            result = build_source_graph_use_case(
                args.name,
                args.graph_name,
                database=args.database,
                namespace=args.namespace,
            )
            _render_graph_summary(result.graph, output_format=args.format, path=result.path)
            return 0

        if args.command == "source" and args.source_command == "refresh":
            result = refresh_source_graph_use_case(
                args.name,
                args.graph_name,
                namespace=args.namespace,
            )
            if args.format == "json":
                print(
                    json.dumps(
                        {
                            "graph": result.graph.to_dict(),
                            "path": str(result.path),
                            "refresh": result.report.to_dict(),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                _render_graph_summary(result.graph, output_format="text", path=result.path)
                print(f"Changes: {len(result.report.changes)}")
            return 0

        if args.command == "source" and args.source_command == "enrich":
            result = enrich_source_use_case(
                args.name,
                args.graph_name,
                profile_row_limit=args.profile_row_limit,
                sample_limit=args.sample_limit,
                persist_join_candidates=args.persist_join_candidates,
            )
            _render_source_enrichment(result, output_format=args.format)
            if result.workfile.samples_present:
                print(
                    "note: raw sample rows were emitted only in this output "
                    "and were not persisted.",
                    file=sys.stderr,
                )
            return 0 if result.workfile.complete else 1

        if args.command == "workspace" and args.workspace_command == "create":
            result = create_workspace_use_case(args.name, description=args.description)
            _render_workspace(result.workspace, output_format=args.format)
            return 0

        if args.command == "workspace" and args.workspace_command == "list":
            names = list_workspaces_use_case()
            if args.format == "json":
                print(json.dumps({"workspaces": list(names)}, indent=2, sort_keys=True))
            else:
                for name in names:
                    print(name)
            return 0

        if args.command == "workspace" and args.workspace_command == "show":
            result = load_workspace_use_case(args.name)
            _render_workspace(result, output_format=args.format)
            return 0

        if args.command == "workspace" and args.workspace_command == "scope":
            result = resolve_workspace_scope_use_case(
                args.name,
                systems=tuple(args.systems or ()),
                graphs=tuple(args.graphs or ()),
                areas=tuple(args.areas or ()),
                schemas=tuple(args.schemas or ()),
                zones=tuple(args.zones or ()),
            )
            _render_resolved_scope(result, output_format=args.format)
            return 0

        if (
            args.command == "workspace"
            and args.workspace_command == "system"
            and args.workspace_system_command == "define"
        ):
            result = define_workspace_system_use_case(
                args.workspace_name,
                args.system_name,
                graph_names=tuple(args.graphs),
                description=args.description,
            )
            _render_workspace(result.workspace, output_format=args.format)
            return 0

        if (
            args.command == "workspace"
            and args.workspace_command == "area"
            and args.workspace_area_command == "define"
        ):
            result = define_workspace_area_use_case(
                args.workspace_name,
                args.system_name,
                args.area_name,
                schema_references=tuple(args.schemas),
                description=args.description,
            )
            _render_workspace(result.workspace, output_format=args.format)
            return 0

        if (
            args.command == "workspace"
            and args.workspace_command == "zone"
            and args.workspace_zone_command == "define"
        ):
            result = define_workspace_zone_use_case(
                args.workspace_name,
                args.system_name,
                args.zone_name,
                object_references=tuple(args.objects),
                description=args.description,
            )
            _render_workspace(result.workspace, output_format=args.format)
            return 0

        if (
            args.command == "workspace"
            and args.workspace_command == "zone"
            and args.workspace_zone_command == "show"
        ):
            result = show_workspace_zone_use_case(
                args.workspace_name,
                args.system_name,
                args.zone_name,
            )
            _render_resolved_zone(result, output_format=args.format)
            return 0

        if (
            args.command == "workspace"
            and args.workspace_command == "relationship"
            and args.workspace_relationship_command == "add"
        ):
            result = add_workspace_relationship_use_case(
                args.workspace_name,
                source_reference=args.source_reference,
                target_reference=args.target_reference,
                reason=args.reason,
                validated=args.validated,
            )
            _render_workspace_relationships(
                result.workspace.name,
                (result.relationship.to_dict(),),
                output_format=args.format,
            )
            return 0

        if (
            args.command == "workspace"
            and args.workspace_command == "relationship"
            and args.workspace_relationship_command == "list"
        ):
            workspace = load_workspace_use_case(args.workspace_name)
            _render_workspace_relationships(
                workspace.name,
                tuple(item.to_dict() for item in workspace.relationships),
                output_format=args.format,
            )
            return 0

        if (
            args.command == "workspace"
            and args.workspace_command == "relationship"
            and args.workspace_relationship_command in {"validate", "reject"}
        ):
            result = decide_workspace_relationship_use_case(
                args.workspace_name,
                args.relationship_id,
                state=(
                    "validated" if args.workspace_relationship_command == "validate" else "rejected"
                ),
                reason=args.reason,
            )
            _render_workspace_relationships(
                result.workspace.name,
                (result.relationship.to_dict(),),
                output_format=args.format,
            )
            return 0

        if args.command == "connector" and args.connector_command == "check":
            result = check_connector_use_case(args.name)
            _render_check(result, output_format=args.format)
            return 0 if result.available else 1

        if args.command == "connector" and args.connector_command == "probe":
            result = probe_connector_use_case(
                args.name,
                config_path=args.config,
                database=args.database,
            )
            _render_probe(result, output_format=args.format)
            return 0

        if args.command == "connector" and args.connector_command == "discover":
            result = discover_catalog_use_case(
                args.name,
                config_path=args.config,
                database=args.database,
                namespace=args.namespace,
            )
            _render_catalog(result, output_format=args.format)
            return 0

        if args.command == "connector" and args.connector_command == "scaffold":
            result = scaffold_connector_use_case(args.name, output=args.output)
            print(f"Created connector candidate: {result.path}")
            print("Next: read CONNECTOR_TASK.md and implement probe plus discover_catalog.")
            return 0

        if args.command == "connector" and args.connector_command == "sample":
            result = sample_connector_use_case(
                args.name,
                config_path=args.config,
                database=args.database,
                namespace=args.namespace,
                object_name=args.object_name,
                limit=args.limit,
            )
            _render_sample(result, output_format=args.format)
            return 0

        if args.command == "connector" and args.connector_command == "profile":
            result = profile_connector_use_case(
                args.name,
                config_path=args.config,
                database=args.database,
                namespace=args.namespace,
                object_name=args.object_name,
                row_limit=args.row_limit,
                small_domain_limit=args.small_domain_limit,
                include_values=args.include_values,
            )
            _render_profile(result, output_format=args.format)
            return 0

        if args.command == "knowledge" and args.knowledge_command == "add":
            result = add_knowledge_document_use_case(
                args.id,
                args.path,
                scope_reference=args.scope,
                title=args.title,
                state=args.state,
                workspace_name=args.workspace,
                replace_existing=args.replace,
            )
            payload = {**result.document.to_dict(include_content=False), "path": str(result.path)}
            _render_knowledge(payload, output_format=args.format)
            return 0

        if args.command == "knowledge" and args.knowledge_command == "list":
            documents = list_knowledge_documents_use_case()
            payload = {
                "count": len(documents),
                "documents": [item.to_dict(include_content=False) for item in documents],
            }
            _render_knowledge(payload, output_format=args.format)
            return 0

        if args.command == "knowledge" and args.knowledge_command == "show":
            document = load_knowledge_document_use_case(args.id)
            _render_knowledge(document.to_dict(), output_format=args.format)
            return 0

        if args.command == "knowledge" and args.knowledge_command == "resolve":
            context = resolve_knowledge_use_case(
                args.graph,
                args.object,
                mode=args.mode,
                document_ids=tuple(args.documents or ()),
                workspace_name=args.workspace,
                max_characters=args.max_characters,
            )
            _render_knowledge(context.to_dict(), output_format=args.format)
            return 0

        if args.command == "provider" and args.provider_command == "list":
            results = tuple(
                check_provider_use_case(name) for name in list_provider_names_use_case()
            )
            _render_provider_checks(results, output_format=args.format)
            return 0

        if args.command == "provider" and args.provider_command == "check":
            result = check_provider_use_case(args.name)
            _render_provider_checks((result,), output_format=args.format)
            return 0 if result.configured else 1

        if args.command == "provider" and args.provider_command == "configure":
            adapter = args.adapter or BUILTIN_PROVIDER_ADAPTERS.get(args.name)
            api_key = None
            if args.from_env:
                api_key = _provider_api_key(
                    args.name,
                    adapter=adapter or "",
                    from_env=True,
                )
            elif not args.no_api_key and adapter in {
                "openai-compatible",
                "openrouter",
            }:
                api_key = _provider_api_key(
                    args.name,
                    adapter=adapter,
                    from_env=False,
                )
            path = configure_provider_use_case(
                args.name,
                adapter=adapter,
                api_key=api_key,
                model=args.model,
                base_url=args.base_url,
                reasoning_effort=args.reasoning_effort,
                structured_mode=args.structured_mode,
                allow_no_api_key=args.no_api_key,
            )
            print(f"Configured provider {args.name}: {path}")
            return 0

        if args.command == "provider" and args.provider_command == "test":
            result = test_provider_use_case(args.name, timeout=args.timeout)
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(f"Provider: {result['name']}")
                print("Status: ok")
            return 0

        if args.command == "provider" and args.provider_command == "scaffold":
            result = scaffold_provider_use_case(args.name, output=args.output)
            print(f"Created provider adapter candidate: {result.path}")
            print("Next: read PROVIDER_TASK.md, implement, test, and review before installation.")
            return 0

        if args.command == "model" and args.model_command == "download":
            result = download_embedding_model_use_case(
                name=args.name,
                target=args.target,
                force=args.force,
                progress=_model_download_progress,
            )
            payload = {
                "model": result.spec.name,
                "path": str(result.path),
                "reused": result.reused,
                "sha256": result.spec.sha256,
                "size": result.spec.size,
                "source": result.spec.source,
            }
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"Model: {result.spec.name}")
                print(f"Status: {'already present' if result.reused else 'downloaded'}")
                print(f"Path: {result.path}")
                print(f"SHA-256: {result.spec.sha256}")
            return 0

        if args.command == "model" and args.model_command == "status":
            payload = embedding_model_status_use_case(
                name=args.name,
                model_path=args.model_path,
            )
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"Model: {payload['model']}")
                print(f"Path: {payload['path']}")
                print(f"Status: {'ready' if payload['sha256_valid'] else 'missing or invalid'}")
            return 0 if payload["sha256_valid"] else 1

        if args.command == "index" and args.index_command == "build":
            result = build_retrieval_index_use_case(
                args.name,
                model_path=args.model_path,
                batch_size=args.batch_size,
                n_threads=args.n_threads,
                progress=_index_build_progress,
            )
            payload = {"index": result.metadata.to_dict(), "path": str(result.path)}
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"Index: {result.metadata.graph}")
                print(f"Documents: {result.metadata.document_count}")
                print(f"Dimensions: {result.metadata.dimensions}")
                print(f"Model: {result.metadata.model_id}")
                print(f"Path: {result.path}")
            return 0

        if args.command == "index" and args.index_command == "status":
            payload = retrieval_index_status_use_case(args.name)
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                metadata = payload["index"]
                print(f"Index: {args.name}")
                print(f"Status: {'ready' if payload['current'] else 'stale'}")
                if isinstance(metadata, dict):
                    print(f"Documents: {metadata['document_count']}")
                    print(f"Dimensions: {metadata['dimensions']}")
                    print(f"Model: {metadata['model_id']}")
                print(f"Path: {payload['path']}")
            return 0 if payload["current"] else 1

        if args.command == "graph" and args.graph_command == "build":
            result = build_graph_use_case(
                args.name,
                connector_name=args.connector,
                config_path=args.config,
                database=args.database,
                namespace=args.namespace,
            )
            _render_graph_summary(result.graph, output_format=args.format, path=result.path)
            return 0

        if args.command == "graph" and args.graph_command == "refresh":
            result = refresh_graph_use_case(
                args.name,
                config_path=args.config,
                namespace=args.namespace,
            )
            payload = {
                "change_report_path": (
                    str(result.change_report_path) if result.change_report_path else None
                ),
                "graph": result.graph.name,
                "path": str(result.path),
                "refresh": result.report.to_dict(),
                "workspace_impacts": [impact.to_dict() for impact in result.workspace_impacts],
            }
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"Refreshed graph: {result.graph.name}")
                print(f"Changes: {len(result.report.changes)}")
                for change in result.report.changes:
                    values = ""
                    if change.before is not None or change.after is not None:
                        values = f" ({change.before!r} -> {change.after!r})"
                    print(f"- {change.kind}: {change.reference} [{change.severity}]{values}")
                print(
                    "Review required: "
                    f"annotations={result.report.review_required_annotations}, "
                    f"relationships={result.report.review_required_relationships}"
                )
                print(f"Preserved stale claims: {len(result.report.stale_claims)}")
                for impact in result.workspace_impacts:
                    print(
                        f"Workspace impact: {impact.workspace}/{impact.system}; "
                        f"areas={', '.join(impact.areas) or '-'}; "
                        f"zones={', '.join(impact.zones) or '-'}"
                    )
                print(f"Path: {result.path}")
                report_path = result.change_report_path or "not written (no revision change)"
                print(f"Change report: {report_path}")
            return 0

        if args.command == "graph" and args.graph_command == "list":
            names = list_graphs_use_case()
            if args.format == "json":
                print(json.dumps({"graphs": list(names)}, indent=2, sort_keys=True))
            else:
                for name in names:
                    print(name)
            return 0

        if args.command == "graph" and args.graph_command == "show":
            graph_document = load_graph_use_case(args.name)
            if args.format == "json":
                print(json.dumps(graph_document.to_dict(), indent=2, sort_keys=True))
            else:
                _render_graph_summary(graph_document, output_format="text")
            return 0

        if args.command == "search":
            search_arguments = {
                "limit": args.limit,
                "mode": args.mode,
                "model_path": args.model_path,
                "n_threads": args.n_threads,
                "annotation_states": (
                    frozenset(args.annotation_states) if args.annotation_states else None
                ),
                "validated_only": args.validated_only,
            }
            if args.workspace:
                if args.namespace is not None:
                    raise WorkspaceFailure(
                        "invalid_workspace_scope",
                        "Use --scope-schema GRAPH:NAMESPACE instead of --namespace "
                        "for a workspace.",
                    )
                results = search_workspace_use_case(
                    args.name,
                    args.query,
                    systems=tuple(args.systems or ()),
                    graphs=tuple(args.graphs or ()),
                    areas=tuple(args.areas or ()),
                    schemas=tuple(args.schemas or ()),
                    zones=tuple(args.zones or ()),
                    **search_arguments,
                )
            else:
                results = search_graph_use_case(
                    args.name,
                    args.query,
                    namespace=args.namespace,
                    **search_arguments,
                )
            _render_search_results(results, output_format=args.format)
            return 0

        if args.command == "grounding":
            grounding_arguments = {
                "lineage_names": tuple(args.lineages or ()),
                "source_names": tuple(args.sources or ()),
                "trace_reference": args.trace_reference,
                "lineage_limit": args.lineage_limit,
                "lineage_mode": args.lineage_mode,
                "lineage_states": (frozenset(args.lineage_states) if args.lineage_states else None),
                "seed_limit": args.seed_limit,
                "max_objects": args.max_objects,
                "max_joins": args.max_joins,
                "max_hops": args.max_hops,
                "max_trace_hops": args.max_trace_hops,
                "max_fields_per_object": args.max_fields_per_object,
                "max_characters": args.max_characters,
                "mode": args.mode,
                "model_path": args.model_path,
                "n_threads": args.n_threads,
                "annotation_states": (
                    frozenset(args.annotation_states) if args.annotation_states else None
                ),
                "validated_only": args.validated_only,
            }
            if args.workspace:
                if args.namespace is not None:
                    raise WorkspaceFailure(
                        "invalid_workspace_scope",
                        "Use --scope-schema GRAPH:NAMESPACE instead of --namespace "
                        "for a workspace.",
                    )
                result = compile_workspace_grounding_use_case(
                    args.name,
                    args.query,
                    systems=tuple(args.systems or ()),
                    graphs=tuple(args.graphs or ()),
                    areas=tuple(args.areas or ()),
                    schemas=tuple(args.schemas or ()),
                    zones=tuple(args.zones or ()),
                    **grounding_arguments,
                )
            else:
                result = compile_graph_grounding_use_case(
                    args.name,
                    args.query,
                    namespace=args.namespace,
                    **grounding_arguments,
                )
            _render_grounding(result, output_format=args.format)
            return 0

        if args.command == "context" and args.context_command == "build":
            context_arguments = {
                "seed_limit": args.seed_limit,
                "max_objects": args.max_objects,
                "max_joins": args.max_joins,
                "max_hops": args.max_hops,
                "max_fields_per_object": args.max_fields_per_object,
                "max_characters": args.max_characters,
                "mode": args.mode,
                "model_path": args.model_path,
                "n_threads": args.n_threads,
                "annotation_states": (
                    frozenset(args.annotation_states) if args.annotation_states else None
                ),
                "validated_only": args.validated_only,
            }
            if args.workspace:
                if args.namespace is not None:
                    raise WorkspaceFailure(
                        "invalid_workspace_scope",
                        "Use --scope-schema GRAPH:NAMESPACE instead of --namespace "
                        "for a workspace.",
                    )
                result = compile_workspace_context_use_case(
                    args.name,
                    args.query,
                    systems=tuple(args.systems or ()),
                    graphs=tuple(args.graphs or ()),
                    areas=tuple(args.areas or ()),
                    schemas=tuple(args.schemas or ()),
                    zones=tuple(args.zones or ()),
                    **context_arguments,
                )
            else:
                result = compile_context_use_case(
                    args.name,
                    args.query,
                    namespace=args.namespace,
                    **context_arguments,
                )
            _render_context(result, output_format=args.format)
            return 0

        if args.command == "context" and args.context_command == "prefix":
            prefix_arguments = {
                "max_objects": args.max_objects,
                "max_joins": args.max_joins,
                "max_fields_per_object": args.max_fields_per_object,
                "max_characters": args.max_characters,
                "annotation_states": (
                    frozenset(args.annotation_states) if args.annotation_states else None
                ),
                "validated_only": args.validated_only,
            }
            if args.workspace:
                if args.namespace is not None:
                    raise WorkspaceFailure(
                        "invalid_workspace_scope",
                        "Use --scope-schema GRAPH:NAMESPACE instead of --namespace "
                        "for a workspace.",
                    )
                result = compile_workspace_context_prefix_use_case(
                    args.name,
                    systems=tuple(args.systems or ()),
                    graphs=tuple(args.graphs or ()),
                    areas=tuple(args.areas or ()),
                    schemas=tuple(args.schemas or ()),
                    zones=tuple(args.zones or ()),
                    **prefix_arguments,
                )
            else:
                result = compile_context_prefix_use_case(
                    args.name,
                    namespace=args.namespace,
                    **prefix_arguments,
                )
            _render_context(result, output_format=args.format)
            return 0

        if args.command == "context" and args.context_command == "diff":
            result = diff_context_packets_use_case(args.left, args.right)
            _render_context_diff(result.to_dict(), output_format=args.format)
            return 0

        if args.command == "context" and args.context_command == "impact":
            result = context_packet_impact_use_case(args.packet, args.graph)
            _render_context_impact(result.to_dict(), output_format=args.format)
            return 0 if result.status != "unknown" else 1

        if args.command == "graph" and args.graph_command == "annotate":
            objects = set(args.objects or [])
            if args.dry_run:
                tasks = plan_annotations_use_case(
                    args.name,
                    namespace=args.namespace,
                    objects=objects,
                    limit=args.limit,
                    missing_only=not args.include_annotated,
                    sample_limit=args.samples,
                    profile_row_limit=args.profile_rows,
                    include_small_domain_values=args.include_small_domain_values,
                    config_path=args.config,
                    knowledge_mode=args.knowledge,
                    knowledge_document_ids=tuple(args.knowledge_documents or ()),
                    knowledge_workspace=args.knowledge_workspace,
                    max_knowledge_characters=args.max_knowledge_characters,
                )
                _render_annotation_plan(tasks, output_format=args.format)
                return 0
            if args.samples:
                print(
                    f"warning: up to {args.samples} sampled rows per object will be "
                    f"sent to provider profile {args.provider}",
                    file=sys.stderr,
                )
            if args.profile_rows:
                detail = (
                    " with permission for complete small-domain values"
                    if args.include_small_domain_values
                    else ""
                )
                print(
                    f"warning: bounded column profiles{detail} will be sent to "
                    f"provider profile {args.provider}",
                    file=sys.stderr,
                )
            if args.knowledge == "scoped" or args.knowledge_documents:
                print(
                    "warning: bounded knowledge documents will be sent to "
                    f"provider profile {args.provider}",
                    file=sys.stderr,
                )
            result = run_annotation_batch_use_case(
                args.name,
                provider_name=args.provider,
                namespace=args.namespace,
                objects=objects,
                limit=args.limit,
                missing_only=not args.include_annotated,
                workers=args.workers,
                retry=args.retry,
                retry_backoff=args.retry_backoff,
                skip_errors=args.skip_errors,
                max_errors=args.max_errors,
                model=args.model,
                timeout=args.timeout,
                sample_limit=args.samples,
                profile_row_limit=args.profile_rows,
                include_small_domain_values=args.include_small_domain_values,
                config_path=args.config,
                knowledge_mode=args.knowledge,
                knowledge_document_ids=tuple(args.knowledge_documents or ()),
                knowledge_workspace=args.knowledge_workspace,
                max_knowledge_characters=args.max_knowledge_characters,
                progress=_print_annotation_progress,
            )
            payload = {**result.run.to_dict(), "graph": result.graph.name, "path": str(result.path)}
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(
                    f"Annotated {result.run.annotated}/{result.run.planned} objects "
                    f"({result.run.failed} failed)."
                )
                print(f"Path: {result.path}")
            return 0

        if args.command == "annotation" and args.annotation_command == "plan":
            _require_graph_or_focus(args.name, args.focus)
            if args.focus:
                tasks = plan_focus_annotations_use_case(
                    args.focus,
                    namespace=args.namespace,
                    objects=set(args.objects or []),
                    limit=args.limit,
                    missing_only=not args.include_annotated,
                    knowledge_mode=args.knowledge,
                    knowledge_document_ids=tuple(args.knowledge_documents or ()),
                    knowledge_workspace=args.knowledge_workspace,
                    max_knowledge_characters=args.max_knowledge_characters,
                )
            else:
                tasks = plan_annotations_use_case(
                    args.name,
                    namespace=args.namespace,
                    objects=set(args.objects or []),
                    limit=args.limit,
                    missing_only=not args.include_annotated,
                    knowledge_mode=args.knowledge,
                    knowledge_document_ids=tuple(args.knowledge_documents or ()),
                    knowledge_workspace=args.knowledge_workspace,
                    max_knowledge_characters=args.max_knowledge_characters,
                )
            _render_annotation_plan(tasks, output_format=args.format)
            return 0

        if args.command == "annotation" and args.annotation_command == "next":
            _require_graph_or_focus(args.name, args.focus)
            if args.samples:
                print(
                    f"warning: including up to {args.samples} sampled rows "
                    "in the coding-agent task",
                    file=sys.stderr,
                )
            if args.profile_rows:
                detail = (
                    " with permission for complete small-domain values"
                    if args.include_small_domain_values
                    else ""
                )
                print(
                    f"warning: bounded column profiles{detail} will be included in the "
                    "coding-agent task",
                    file=sys.stderr,
                )
            if args.knowledge == "scoped" or args.knowledge_documents:
                print(
                    "warning: bounded knowledge documents will be included in the "
                    "coding-agent task",
                    file=sys.stderr,
                )
            if args.focus:
                tasks = plan_focus_annotations_use_case(
                    args.focus,
                    namespace=args.namespace,
                    objects=set(args.objects or []),
                    limit=1,
                    missing_only=not args.include_annotated,
                    sample_limit=args.samples,
                    profile_row_limit=args.profile_rows,
                    include_small_domain_values=args.include_small_domain_values,
                    config_path=args.config,
                    knowledge_mode=args.knowledge,
                    knowledge_document_ids=tuple(args.knowledge_documents or ()),
                    knowledge_workspace=args.knowledge_workspace,
                    max_knowledge_characters=args.max_knowledge_characters,
                )
            else:
                tasks = plan_annotations_use_case(
                    args.name,
                    namespace=args.namespace,
                    objects=set(args.objects or []),
                    limit=1,
                    missing_only=not args.include_annotated,
                    sample_limit=args.samples,
                    profile_row_limit=args.profile_rows,
                    include_small_domain_values=args.include_small_domain_values,
                    config_path=args.config,
                    knowledge_mode=args.knowledge,
                    knowledge_document_ids=tuple(args.knowledge_documents or ()),
                    knowledge_workspace=args.knowledge_workspace,
                    max_knowledge_characters=args.max_knowledge_characters,
                )
            if not tasks:
                print(json.dumps({"status": "complete"}, indent=2, sort_keys=True))
                return 0
            print(json.dumps(tasks[0].to_dict(), indent=2, sort_keys=True))
            return 0

        if args.command == "annotation" and args.annotation_command == "apply":
            payload = _read_json_object(args.input)
            result = apply_annotation_use_case(args.name, payload)
            print(f"Applied draft annotation to {result.target_id}")
            print(f"Path: {result.path}")
            return 0

        if args.command == "annotation" and args.annotation_command == "list":
            states = frozenset(args.state) if args.state else None
            records = list_annotation_reviews_use_case(args.name, states=states)
            _render_annotation_records(records, output_format=args.format)
            return 0

        if args.command == "annotation" and args.annotation_command == "show":
            record = show_annotation_use_case(args.name, args.target)
            _render_annotation_record(record.to_dict(), output_format=args.format)
            return 0

        if args.command == "annotation" and args.annotation_command == "edit":
            patch = _read_json_object(args.input)
            result = edit_annotation_use_case(
                args.name,
                args.target,
                patch,
                reason=args.reason,
            )
            _render_annotation_record(
                {**result.record.to_dict(), "path": str(result.path)},
                output_format=args.format,
            )
            return 0

        if args.command == "annotation" and args.annotation_command in {
            "validate",
            "reject",
            "defer",
        }:
            state = {
                "validate": "validated",
                "reject": "rejected",
                "defer": "deferred",
            }[args.annotation_command]
            result = decide_annotation_use_case(
                args.name,
                args.target,
                state=state,
                reason=args.reason,
                include_fields=args.include_fields,
            )
            _render_annotation_change(
                result.records,
                path=result.path,
                output_format=args.format,
            )
            return 0

        if args.command == "relationship" and args.relationship_command == "add":
            result = add_relationship_use_case(
                args.name,
                from_reference=args.from_reference,
                to_reference=args.to_reference,
                reason=args.reason,
                validated=args.validated,
            )
            payload = {
                "edge": result.edge.to_dict(),
                "graph": result.graph.name,
                "path": str(result.path),
            }
            _render_relationship_payload(payload, output_format=args.format)
            return 0

        if args.command == "relationship" and args.relationship_command == "check":
            profile = check_relationship_use_case(
                args.name,
                from_reference=args.from_reference,
                to_reference=args.to_reference,
                config_path=args.config,
                row_limit=args.row_limit,
            )
            _render_relationship_profile(profile, output_format=args.format)
            return 0

        if args.command == "relationship" and args.relationship_command == "discover":
            result = discover_relationships_use_case(
                args.name,
                object_reference=args.object_reference,
                field_name=args.field_name,
                config_path=args.config,
                max_pairs=args.max_pairs,
                row_limit=args.row_limit,
                min_source_coverage=args.min_source_coverage,
                min_overlap_count=args.min_overlap_count,
                min_target_uniqueness=args.min_target_uniqueness,
                persist=not args.dry_run,
                focus_name=args.focus,
                expand_one_hop=args.expand_one_hop,
            )
            payload = {
                "candidates": [edge.to_dict() for edge in result.candidates],
                "dry_run": args.dry_run,
                "graph": result.graph.name,
                "path": str(result.path) if result.path else None,
                "probed_pairs": len(result.profiles),
            }
            _render_relationship_payload(payload, output_format=args.format)
            return 0

        if args.command == "relationship" and args.relationship_command == "list":
            edges = list_relationships_use_case(args.name)
            payload = {"candidates": [edge.to_dict() for edge in edges], "graph": args.name}
            _render_relationship_payload(payload, output_format=args.format)
            return 0

        if args.command == "relationship" and args.relationship_command in {"validate", "reject"}:
            state = "validated" if args.relationship_command == "validate" else "rejected"
            result = decide_relationship_use_case(
                args.name,
                edge_id=args.edge_id,
                state=state,
                reason=args.reason,
            )
            payload = {
                "edge": result.edge.to_dict(),
                "graph": result.graph.name,
                "path": str(result.path),
            }
            _render_relationship_payload(payload, output_format=args.format)
            return 0
    except (
        AnnotationFailure,
        ConnectorFailure,
        ContextFailure,
        DemoFailure,
        FocusFailure,
        GraphFailure,
        KnowledgeFailure,
        LineageFailure,
        ProviderFailure,
        RelationshipFailure,
        RetrievalFailure,
        SearchFailure,
        SemanticFailure,
        SourceFailure,
        WorkspaceFailure,
    ) as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 0


def _add_format_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )


def _add_annotation_knowledge_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--knowledge",
        choices=("none", "scoped"),
        default="none",
        help="Include no automatic documents (default) or resolve matching scopes.",
    )
    parser.add_argument(
        "--knowledge-document",
        action="append",
        dest="knowledge_documents",
        help="Include one document explicitly; repeat for more.",
    )
    parser.add_argument(
        "--knowledge-workspace",
        help="Workspace used to resolve system-scoped documents.",
    )
    parser.add_argument(
        "--max-knowledge-characters",
        type=int,
        default=DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    )


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--system",
        action="append",
        dest="systems",
        help="Include a system; repeat to include multiple systems.",
    )
    parser.add_argument(
        "--graph",
        action="append",
        dest="graphs",
        help="Limit the scope to a graph; repeat to include multiple graphs.",
    )
    parser.add_argument(
        "--area",
        action="append",
        dest="areas",
        help="Limit to an area (NAME or SYSTEM:NAME); repeat for a union.",
    )
    parser.add_argument(
        "--schema",
        action="append",
        dest="schemas",
        help="Limit to a schema as GRAPH:NAMESPACE; repeat for a union.",
    )
    parser.add_argument(
        "--zone",
        action="append",
        dest="zones",
        help="Limit to a zone (NAME or SYSTEM:NAME); repeat for a union.",
    )


def _add_workspace_retrieval_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        action="store_true",
        help="Treat NAME as a workspace and search only its resolved scope.",
    )
    parser.add_argument("--system", action="append", dest="systems")
    parser.add_argument("--graph", action="append", dest="graphs")
    parser.add_argument("--area", action="append", dest="areas")
    parser.add_argument(
        "--scope-schema",
        action="append",
        dest="schemas",
        help="Workspace schema as GRAPH:NAMESPACE; repeat for a union.",
    )
    parser.add_argument("--zone", action="append", dest="zones")


def _add_annotation_state_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--annotation-state",
        action="append",
        dest="annotation_states",
        choices=("draft", "validated", "rejected", "deferred", "review_required"),
        help="Include semantic annotations in one or more review states.",
    )
    parser.add_argument(
        "--validated-only",
        action="store_true",
        help="Include only human-validated semantic annotations.",
    )


def _render_relationship_profile(
    profile: RelationshipPairProfile,
    *,
    output_format: str,
) -> None:
    if output_format == "json":
        print(json.dumps(profile.to_dict(), indent=2, sort_keys=True))
        return
    pair = profile.pair
    print(
        f"Pair: {pair.from_namespace}.{pair.from_object}.{pair.from_field} -> "
        f"{pair.to_namespace}.{pair.to_object}.{pair.to_field}"
    )
    print(f"Profile row limit: {profile.profile_row_limit}")
    print(f"Overlap count: {profile.overlap_count}")
    print(f"Source coverage: {profile.source_coverage:.1%}")
    print(f"Target coverage: {profile.target_coverage:.1%}")
    print(f"Target uniqueness: {profile.target_uniqueness:.1%}")


def _render_search_results(results: SearchResults, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(results.to_dict(), indent=2, sort_keys=True))
        return
    print(f"Search: {results.query}")
    if results.workspace:
        print(f"Workspace: {results.workspace}")
        print(f"Scope hash: {results.scope_hash}")
        print(f"Graphs: {', '.join(results.graphs)}")
    else:
        print(f"Graph: {results.graph}")
    print(f"Mode: {results.mode}")
    print(f"Terms: {', '.join(results.terms)}")
    for hit in results.hits:
        graph = f"; graph={hit.source_graph}" if hit.source_graph else ""
        print(f"- {hit.label} [{hit.type}{graph}] score={hit.score}")
        print(f"  Reasons: {', '.join(hit.reasons)}")
        if hit.fields:
            fields = ", ".join(f"{field.label} ({field.score})" for field in hit.fields)
            print(f"  Fields: {fields}")


def _render_context(result: ContextResult, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print("# TAREL Context")
    print(f"Contract: {result.contract_version}")
    print(f"Graph: {result.graph}")
    print(f"Revision: {result.graph_revision}")
    print(f"Stable hash: {result.stable_hash}")
    print(f"Scope: {result.scope.mode}; namespace={result.scope.namespace or '*'}")
    if result.scope.workspace:
        print(f"Workspace: {result.scope.workspace}")
        print(f"Scope hash: {result.scope.scope_hash}")
        print(f"Graphs: {', '.join(result.scope.graphs)}")
    print(f"Annotation states: {', '.join(sorted(result.annotation_states))}")

    print("\n## Stable objects")
    for item in sorted(result.objects, key=lambda candidate: candidate.id):
        state = f"; annotation={item.annotation_state}" if item.annotation_state else ""
        print(f"\n### {item.label} [{item.type}{state}]")
        if item.description:
            print(item.description)
        if item.role:
            print(f"Role: {item.role}")
        if item.grain:
            print(f"Grain: {item.grain}")
        for warning in item.warnings:
            print(f"Warning: {warning}")
        for field in sorted(item.fields, key=lambda candidate: candidate.id):
            nullable = " nullable" if field.nullable else ""
            details = [value for value in (field.role, field.semantic_type) if value]
            detail = f" [{', '.join(details)}]" if details else ""
            print(f"- {field.name}: {field.data_type}{nullable}{detail}")
            if field.description:
                print(f"  {field.description}")

    print("\n## Stable joins")
    for join in sorted(result.joins, key=lambda candidate: candidate.id):
        print(
            f"- {join.from_object}({', '.join(join.from_fields)}) -> "
            f"{join.to_object}({', '.join(join.to_fields)}) "
            f"[{join.kind}; {join.state}]"
        )

    print("\n## Dynamic request")
    print(f"Dynamic hash: {result.dynamic_hash}")
    print(f"Packet hash: {result.packet_hash}")
    print(
        "Question: <none; query-independent prefix>"
        if result.retrieval_mode == "scope"
        else f"Question: {result.query}"
    )
    print(f"Retrieval: {result.retrieval_mode}")
    print(f"Terms: {', '.join(result.terms)}")
    print(
        f"Context characters: {result.context_characters}/{result.max_characters}; "
        f"stable={result.stable_characters}; "
        f"objects={len(result.objects)}/{result.max_objects}; "
        f"joins={len(result.joins)}/{result.max_joins}"
    )
    print("Selection:")
    for item in result.objects:
        score = f", score={item.search_score}" if item.search_score is not None else ""
        print(
            f"- {item.label}: {item.selection}, distance={item.distance}{score}, "
            f"omitted_fields={item.omitted_fields}"
        )
        for field in item.fields:
            print(f"  - {field.name}: {', '.join(field.reasons)}")
    omission_reasons = ", ".join(result.omissions.reasons) or "none"
    print(
        "Omissions: "
        f"objects={result.omissions.objects}, fields={result.omissions.fields}, "
        f"joins={result.omissions.joins}, paths={result.omissions.paths}; "
        f"reasons={omission_reasons}"
    )
    if result.paths:
        print("Expansion paths:")
        for path in result.paths:
            print(f"- {' -> '.join(path.objects)}")


def _render_grounding(result: GroundingBundle, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(result.stable_prompt(), end="")
    print()
    print(result.dynamic_prompt(), end="")


def _render_context_diff(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Identical: {'yes' if payload['identical'] else 'no'}")
    print(f"Stable changed: {'yes' if payload['stable_changed'] else 'no'}")
    print(f"Dynamic changed: {'yes' if payload['dynamic_changed'] else 'no'}")
    print(f"Graph revision changed: {'yes' if payload['graph_revision_changed'] else 'no'}")
    print(f"Scope changed: {'yes' if payload['scope_changed'] else 'no'}")
    print(f"Query changed: {'yes' if payload['query_changed'] else 'no'}")
    for section in ("objects", "joins"):
        changes = payload[section]
        if isinstance(changes, dict):
            for change in ("added", "removed", "changed"):
                values = changes.get(change, [])
                rendered = ", ".join(values) if isinstance(values, list) else ""
                print(f"{section.capitalize()} {change}: {rendered or '-'}")


def _render_context_impact(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Status: {payload['status']}")
    print(f"Affected: {payload['affected'] if payload['affected'] is not None else 'unknown'}")
    print(f"Exact: {'yes' if payload['exact'] else 'no'}")
    print(f"Reason: {payload['reason']}")
    changes = payload.get("matched_changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                print(f"- {change.get('kind')}: {change.get('reference')}")


def _render_relationship_payload(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        print(f"Candidates: {len(candidates)}")
        for candidate in candidates:
            if isinstance(candidate, dict):
                metadata = candidate.get("metadata", {})
                if isinstance(metadata, dict):
                    print(
                        f"- {metadata.get('from_namespace')}.{metadata.get('from_object')}."
                        f"{metadata.get('from_field')} -> {metadata.get('to_namespace')}."
                        f"{metadata.get('to_object')}.{metadata.get('to_field')} "
                        f"[{metadata.get('state')}]"
                    )
        if "probed_pairs" in payload:
            print(f"Probed pairs: {payload['probed_pairs']}")
        if payload.get("dry_run"):
            print("Dry run: graph was not changed.")
        elif payload.get("path"):
            print(f"Path: {payload['path']}")
        return
    edge = payload.get("edge")
    if isinstance(edge, dict):
        metadata = edge.get("metadata", {})
        if isinstance(metadata, dict):
            print(f"Relationship: {edge.get('id')}")
            print(f"State: {metadata.get('state')}")
        print(f"Path: {payload.get('path')}")


def _render_check(result: ConnectorCheck, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    print(f"Connector: {result.name}")
    print(f"Status: {'available' if result.available else 'unavailable'}")
    print(f"Capabilities: {', '.join(result.capabilities)}")
    print(f"Permissions: {', '.join(result.permissions)}")
    print(f"Dialect: {result.dialect or ''}")
    if result.references:
        print(f"References: {', '.join(result.references)}")
    if result.missing_dependencies:
        print(f"Missing dependencies: {', '.join(result.missing_dependencies)}")
        print(f"Install: pip install tarel[{result.extra}]")


def _render_probe(result: ProbeResult, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    print(f"Connector: {result.connector}")
    print("Status: ok")
    print(f"Server: {result.server_name or ''}")
    print(f"Database: {result.database_name}")
    print(f"Version: {result.product_version or ''}")
    print(f"Level: {result.product_level or ''}")
    print(f"Edition: {result.edition or ''}")
    print(f"Capabilities: {', '.join(result.capabilities)}")
    print(f"Read only: {'yes' if result.read_only else 'no'}")


def _render_catalog(result: CatalogResult, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    print(f"Connector: {result.connector}")
    print("Status: ok")
    print(f"Catalog: {result.catalog}")
    print(f"Dialect: {result.dialect or ''}")
    print(f"Objects: {len(result.objects)}")
    for item in result.objects:
        print(f"{item.namespace}.{item.name} [{item.kind}]")
        for field in item.fields:
            nullable = " nullable" if field.nullable else ""
            print(f"  {field.position}: {field.name} {field.data_type}{nullable}")


def _render_sample(result: SampleResult, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    print(f"Object: {result.namespace}.{result.object_name}")
    print(f"Rows: {len(result.rows)}")
    print(f"Selected fields: {', '.join(result.selected_fields)}")
    print(f"Omitted fields: {', '.join(result.omitted_fields)}")
    print(f"Ordered by: {', '.join(result.ordered_by) if result.ordered_by else 'unordered'}")
    print(f"Truncated values: {'yes' if result.truncated_values else 'no'}")
    for row in result.rows:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))


def _render_profile(result: ObjectProfileResult, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    print(f"Object: {result.namespace}.{result.object_name}")
    print(f"Rows profiled: {result.rows_profiled}")
    print(f"Complete: {'yes' if result.complete else 'no'}")
    print(f"Ordered by: {', '.join(result.ordered_by)}")
    print(f"Values included: {'yes' if result.includes_values else 'no'}")
    for column in result.columns:
        if column.status != "profiled":
            print(f"{column.name} [{column.data_type}]: omitted ({column.reason})")
            continue
        print(
            f"{column.name} [{column.data_type}]: non-null={column.non_null_count}, "
            f"null={column.null_count}, distinct={column.distinct_count}, "
            f"min={column.min_value!r}, max={column.max_value!r}"
        )
        if column.values:
            rendered = ", ".join(f"{item.value!r} ({item.count})" for item in column.values)
            print(f"  values: {rendered}")


def _provider_api_key(name: str, *, adapter: str, from_env: bool) -> str:
    if from_env:
        profile_name = name.upper().replace("-", "_")
        candidates = [
            os.getenv(f"TAREL_PROVIDER_{profile_name}_API_KEY"),
            os.getenv("TAREL_PROVIDER_API_KEY"),
        ]
        if adapter == "openrouter":
            candidates.extend(
                [os.getenv("TAREL_OPENROUTER_API_KEY"), os.getenv("OPENROUTER_API_KEY")]
            )
        elif name == "openai":
            candidates.append(os.getenv("OPENAI_API_KEY"))
        value = next((candidate for candidate in candidates if candidate), None)
        if not value:
            raise ProviderFailure(
                "missing_api_key",
                f"No API key for provider profile {name} was found in the environment.",
            )
        return value
    return getpass.getpass(f"API key for provider profile {name}: ").strip()


def _render_provider_checks(
    results: tuple[ProviderCheck, ...],
    *,
    output_format: str,
) -> None:
    if output_format == "json":
        print(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))
        return
    for result in results:
        print(f"Provider: {result.name}")
        print(f"Adapter: {result.adapter or ''}")
        print(f"Status: {'configured' if result.configured else 'not configured'}")
        print(f"Model: {result.model or ''}")
        print(f"Base URL: {result.base_url or ''}")
        print(f"Config: {result.config_path}")


def _render_workspace(workspace: WorkspaceDocument, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(workspace.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(f"Workspace: {workspace.name}")
    if workspace.description:
        print(f"Description: {workspace.description}")
    for system in sorted(workspace.systems, key=lambda item: item.name):
        print(f"System: {system.name}")
        if system.description:
            print(f"  Description: {system.description}")
        print(f"  Graphs: {', '.join(sorted(system.graphs))}")
        for area in sorted(system.areas, key=lambda item: item.name):
            print(f"  Area: {area.name}")
            for schema in sorted(area.schemas, key=lambda item: (item.graph, item.namespace)):
                print(f"    Schema: {schema.graph}:{schema.namespace}")
        for zone in sorted(system.zones, key=lambda item: item.name):
            print(f"  Zone: {zone.name} ({len(zone.members)} objects)")
    if workspace.relationships:
        print(f"Workspace relationships: {len(workspace.relationships)}")
        for relationship in sorted(workspace.relationships, key=lambda item: item.id):
            print(f"  {relationship.id} [{relationship.state}]")


def _render_source(source: SourceProfile, *, output_format: str) -> None:
    payload = {**source.to_dict(), "revision": source.revision}
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Source: {source.name}")
    print(f"Connector: {source.connector}")
    print(f"Config reference: {source.config_reference or 'connector environment'}")
    print(f"Database: {source.database or 'connector default'}")
    print(f"Namespace: {source.namespace or 'all'}")
    print(f"Graphs: {', '.join(source.graphs) or 'none'}")
    print(
        "Enrichment permissions: "
        f"{', '.join(source.enrichment_permissions) or 'none'}"
    )
    print("Read only: yes")
    print(f"Revision: {source.revision}")


def _render_source_enrichment(
    result: SourceEnrichmentResult,
    *,
    output_format: str,
) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return
    workfile = result.workfile
    print(f"Source: {workfile.source}")
    print(f"Graph: {workfile.graph}")
    print(f"Permissions: {', '.join(workfile.permissions)}")
    print(f"Objects: {len(workfile.objects)}")
    print(f"Complete: {'yes' if workfile.complete else 'no'}")
    for item in workfile.objects:
        print(f"- {item.label}")
        if item.profile is not None:
            print(
                f"  profile: {item.profile.rows_profiled} rows, "
                f"{len(item.profile.columns)} columns"
            )
            for column in item.profile.columns:
                if column.status != "profiled":
                    print(
                        f"    {column.name}: omitted "
                        f"({column.reason or 'unsupported'})"
                    )
                    continue
                print(
                    f"    {column.name}: distinct={column.distinct_count}, "
                    f"min={column.min_value!r}, max={column.max_value!r}"
                )
                if column.values:
                    values = ", ".join(
                        f"{value.value!r} ({value.count})" for value in column.values
                    )
                    print(f"      values: {values}")
        if item.sample is not None:
            print(f"  sample: {len(item.sample.rows)} rows")
            for row in item.sample.rows:
                print(f"    {json.dumps(row, ensure_ascii=False, sort_keys=True)}")
        for pattern in item.key_patterns:
            print(
                f"  pattern: {pattern.field} = {pattern.pattern} "
                f"(coverage {pattern.coverage:.1%})"
            )
        for failure in item.failures:
            print(f"  failure [{failure.operation}/{failure.code}]: {failure.message}")
    print(
        "Transformed join candidates: "
        f"{len(workfile.transformed_join_candidates)}"
    )
    print(f"Persisted candidates: {len(result.persisted_candidates)}")


def _render_source_check(result: SourceCheck, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    print(f"Source: {result.source.name}")
    print(f"Connector: {result.connector.name}")
    print(f"Connector available: {'yes' if result.connector.available else 'no'}")
    print(f"Config status: {result.config_status}")
    print(f"Available: {'yes' if result.available else 'no'}")


def _render_resolved_zone(zone: ResolvedZone, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(zone.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(f"Workspace: {zone.workspace}")
    print(f"System: {zone.system}")
    print(f"Zone: {zone.zone}")
    if zone.description:
        print(f"Description: {zone.description}")
    print(f"Objects: {len(zone.objects)}")
    for item in zone.objects:
        print(f"  {item.graph}:{item.label} [{item.type}; area={item.area}]")


def _render_resolved_scope(scope: ResolvedScope, *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(scope.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(f"Workspace: {scope.workspace}")
    print(f"Scope hash: {scope.scope_hash}")
    print(f"Graphs: {', '.join(scope.graph_names)}")
    print(f"Objects: {len(scope.objects)}")
    for item in scope.objects:
        context = f"system={item.system}; area={item.area or '-'}"
        zones = f"; zones={','.join(item.zones)}" if item.zones else ""
        print(f"  {item.graph}:{item.label} [{item.type}; {context}{zones}]")


def _render_workspace_relationships(
    workspace_name: str,
    relationships: tuple[dict[str, object], ...],
    *,
    output_format: str,
) -> None:
    payload = {"relationships": list(relationships), "workspace": workspace_name}
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(f"Workspace: {workspace_name}")
    print(f"Relationships: {len(relationships)}")
    for relationship in relationships:
        source = relationship["source"]
        target = relationship["target"]
        if not isinstance(source, dict) or not isinstance(target, dict):
            continue
        print(
            f"- {relationship['id']} [{relationship['state']}] "
            f"{source['graph']}:{source['object_id']}."
            f"{','.join(source['fields'])} -> "
            f"{target['graph']}:{target['object_id']}."
            f"{','.join(target['fields'])}"
        )
        print(f"  Reason: {relationship['reason']}")


def _render_graph_summary(
    graph: GraphDocument,
    *,
    output_format: str,
    path: Path | None = None,
) -> None:
    counts = {
        node_type: sum(node.type == node_type for node in graph.nodes)
        for node_type in sorted({node.type for node in graph.nodes})
    }
    payload = {
        "catalog": graph.catalog,
        "dialect": graph.dialect,
        "edges": len(graph.edges),
        "name": graph.name,
        "nodes": len(graph.nodes),
        "node_types": counts,
        "path": str(path) if path else None,
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Graph: {graph.name}")
    print(f"Catalog: {graph.catalog}")
    print(f"Dialect: {graph.dialect or ''}")
    print(f"Nodes: {len(graph.nodes)}")
    print(f"Edges: {len(graph.edges)}")
    print("Node types: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    if path:
        print(f"Path: {path}")


def _render_annotation_plan(
    tasks: tuple[AnnotationTask, ...],
    *,
    output_format: str,
) -> None:
    if output_format == "json":
        print(
            json.dumps(
                {
                    "count": len(tasks),
                    "tasks": [
                        {
                            "context_documents": [
                                item.to_dict() for item in task.context_documents
                            ],
                            "graph_name": task.graph_name,
                            "id": task.id,
                            "target": task.target_label,
                            "target_id": task.target_id,
                        }
                        for task in tasks
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(f"Annotation tasks: {len(tasks)}")
    for task in tasks:
        print(f"{task.id}  {task.graph_name}:{task.target_label}")


def _render_knowledge(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    documents = payload.get("documents")
    if isinstance(documents, list):
        print(f"Knowledge documents: {len(documents)}")
        for item in documents:
            if not isinstance(item, dict):
                continue
            scope = item.get("scope")
            scope_label = _knowledge_scope_label(scope) if isinstance(scope, dict) else "unknown"
            print(
                f"- {item.get('id')} [{item.get('state')}; {scope_label}; "
                f"{item.get('characters', 0)} chars]"
            )
        omitted = payload.get("omitted")
        if isinstance(omitted, list) and omitted:
            print(f"Omitted by budget: {', '.join(str(item) for item in omitted)}")
        return
    scope = payload.get("scope")
    print(f"Knowledge: {payload.get('id', 'resolved context')}")
    if payload.get("title"):
        print(f"Title: {payload['title']}")
    if isinstance(scope, dict):
        print(f"Scope: {_knowledge_scope_label(scope)}")
    if payload.get("state"):
        print(f"State: {payload['state']}")
    if payload.get("revision"):
        print(f"Revision: {payload['revision']}")
    if payload.get("content"):
        print("\n" + str(payload["content"]))


def _knowledge_scope_label(scope: dict[str, object]) -> str:
    kind = str(scope.get("kind") or "unknown")
    graph = scope.get("graph")
    reference = scope.get("reference")
    workspace = scope.get("workspace")
    if kind == "global":
        return "global"
    if kind == "system" and workspace:
        return f"system:{workspace}:{reference}"
    return f"{kind}:{graph}:{reference}" if graph else f"{kind}:{reference}"


def _render_focus(
    focus: FocusDocument,
    *,
    output_format: str,
    path: Path | None = None,
) -> None:
    payload = focus.to_dict()
    if path is not None:
        payload["path"] = str(path)
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Focus: {focus.name}")
    print(f"Seed: {focus.seed}")
    print(f"Revision: {focus.revision}")
    print(f"Sources: {', '.join(f'{item.kind}:{item.name}' for item in focus.sources)}")
    print(
        f"Members: {len(focus.members)}; hops: {len(focus.hops)}; "
        f"truncated: {'yes' if focus.truncated else 'no'}"
    )
    print("Origins:")
    for item in focus.members:
        if item.origin:
            print(f"- {item.reference} [{item.kind}; {item.source}]")
    print("Scope:")
    for item in focus.members:
        reasons = ", ".join(item.reasons)
        print(f"- depth={item.depth} {item.reference} [{item.kind}; {reasons}]")
    for warning in focus.warnings:
        print(f"Warning: {warning}")
    if path is not None:
        print(f"Path: {path}")


def _require_graph_or_focus(graph_name: str | None, focus_name: str | None) -> None:
    if bool(graph_name) == bool(focus_name):
        raise AnnotationFailure(
            "invalid_annotation_scope",
            "Provide exactly one graph NAME or --focus FOCUS.",
        )


def _render_annotation_records(
    records: tuple[AnnotationReviewRecord, ...],
    *,
    output_format: str,
) -> None:
    if output_format == "json":
        print(
            json.dumps(
                {"annotations": [record.to_dict() for record in records], "count": len(records)},
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(f"Annotations: {len(records)}")
    for record in records:
        annotation = record.node.annotation
        state = annotation.state if annotation else "missing"
        print(f"{record.reference}  [{record.node.type}; {state}]")


def _render_annotation_record(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Annotation: {payload['reference']}")
    print(f"Type: {payload['node_type']}")
    annotation = payload.get("annotation")
    if not isinstance(annotation, dict):
        print("State: missing")
        return
    print(f"State: {annotation['state']}")
    print(f"Description: {annotation['description']}")
    if annotation.get("role"):
        print(f"Role: {annotation['role']}")
    if payload.get("grain"):
        print(f"Grain: {payload['grain']}")
    if payload.get("semantic_type"):
        print(f"Semantic type: {payload['semantic_type']}")
    synonyms = annotation.get("synonyms")
    if isinstance(synonyms, list) and synonyms:
        print(f"Synonyms: {', '.join(str(item) for item in synonyms)}")
    warnings = annotation.get("warnings")
    if isinstance(warnings, list) and warnings:
        print(f"Warnings: {', '.join(str(item) for item in warnings)}")
    review = payload.get("review")
    if isinstance(review, dict):
        events = review.get("events")
        if isinstance(events, list):
            print("Review:")
            for event in events:
                if isinstance(event, dict):
                    print(f"- {event.get('action')}: {event.get('reason')}")
    if payload.get("path"):
        print(f"Path: {payload['path']}")


def _render_annotation_change(
    records: tuple[AnnotationReviewRecord, ...],
    *,
    path: Path,
    output_format: str,
) -> None:
    if len(records) == 1:
        _render_annotation_record(
            {**records[0].to_dict(), "path": str(path)},
            output_format=output_format,
        )
        return
    payload = {
        "annotations": [record.to_dict() for record in records],
        "count": len(records),
        "path": str(path),
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Reviewed annotations: {len(records)}")
    for record in records:
        annotation = record.node.annotation
        print(f"- {record.reference} [{annotation.state if annotation else 'missing'}]")
    print(f"Path: {path}")


def _read_json_object(path_value: str) -> dict[str, object]:
    try:
        raw = (
            sys.stdin.read() if path_value == "-" else Path(path_value).read_text(encoding="utf-8")
        )
        payload = json.loads(raw)
    except FileNotFoundError as exc:
        raise AnnotationFailure(
            "proposal_not_found",
            f"Proposal file not found: {path_value}",
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationFailure("invalid_proposal", "Could not read proposal JSON.") from exc
    if not isinstance(payload, dict):
        raise AnnotationFailure("invalid_proposal", "Proposal JSON root must be an object.")
    return payload


def _print_annotation_progress(index: int, total: int, target: str, status: str) -> None:
    print(f"[{index}/{total}] {status} {target}", file=sys.stderr)


def _model_download_progress(downloaded: int, total: int) -> None:
    interval = 25 * 1024 * 1024
    previous = max(0, downloaded - 1024 * 1024)
    if downloaded == total or downloaded // interval != previous // interval:
        percentage = min(100, round(downloaded * 100 / max(total, 1)))
        print(f"Downloading embedding model: {percentage}%", file=sys.stderr)


def _index_build_progress(completed: int, total: int, phase: str) -> None:
    if phase == "embedding":
        print(f"Indexing embeddings: {completed}/{total}", file=sys.stderr)
    elif phase == "writing":
        print("Writing retrieval index...", file=sys.stderr)
    elif phase == "ready":
        print("Retrieval index ready.", file=sys.stderr)
