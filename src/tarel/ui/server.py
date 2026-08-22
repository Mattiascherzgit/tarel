"""Loopback-only HTTP adapter for the optional TAREL browser UI."""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tarel.annotations.contracts import AnnotationFailure
from tarel.annotations.review import resolve_annotation_target
from tarel.application import (
    create_workspace_use_case,
    decide_annotation_use_case,
    define_workspace_area_use_case,
    define_workspace_system_use_case,
    define_workspace_zone_use_case,
    edit_annotation_use_case,
    list_focuses_use_case,
    list_knowledge_documents_use_case,
    list_workspaces_use_case,
    load_focus_use_case,
    load_graph_use_case,
    load_workspace_use_case,
    resolve_workspace_scope_use_case,
)
from tarel.entity_resolution.application import (
    find_entity_resolution_candidates_for_graph_use_case,
)
from tarel.entity_resolution.contracts import EntityResolutionFailure
from tarel.focus.contracts import FocusDocument, FocusFailure
from tarel.focus.core import require_current_focus
from tarel.graph.contracts import GraphDocument, GraphFailure
from tarel.graph.revision import graph_revision
from tarel.knowledge.contracts import KnowledgeContext, KnowledgeFailure
from tarel.knowledge.core import resolve_knowledge
from tarel.lineage.application import (
    add_manual_hop_use_case,
    add_manual_job_use_case,
    decide_lineage_item_use_case,
    load_lineage_use_case,
    trace_upstream_use_case,
)
from tarel.lineage.contracts import LineageDocument, LineageFailure
from tarel.lineage.revision import lineage_revision
from tarel.relationships.core import RelationshipFailure
from tarel.semantics.application import (
    edit_semantic_source_use_case,
    list_semantic_imports_use_case,
    load_semantic_import_use_case,
)
from tarel.semantics.contracts import SemanticFailure
from tarel.ui.presentation import (
    browser_focus_catalog,
    browser_focus_selection,
    browser_graph,
    browser_lineages,
    browser_workspace,
    workspace_revision,
)
from tarel.workspaces.contracts import WorkspaceFailure

_MAX_REQUEST_BYTES = 256 * 1024
_STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


class UIFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class UIConfig:
    graph: str | None = None
    workspace: str | None = None
    systems: tuple[str, ...] = ()
    graphs: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    schemas: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()
    lineages: tuple[str, ...] = ()
    focuses: tuple[str, ...] = ()
    editable: bool = False


class TarelUIBackend:
    def __init__(self, config: UIConfig) -> None:
        self.config = config
        self._lineages = list(config.lineages)
        self._focus_cache: tuple[FocusDocument, ...] | None = None

    def bootstrap(self) -> dict[str, object]:
        documents = tuple(load_lineage_use_case(name) for name in self._lineages)
        workspace = None
        if self.config.workspace:
            workspace = load_workspace_use_case(self.config.workspace)
            scope = self._scope()
            graphs = tuple(load_graph_use_case(name) for name in scope.graph_names)
            semantic_imports = tuple(
                item
                for graph in graphs
                for item in list_semantic_imports_use_case(graph_name=graph.name)
            )
            payload = browser_workspace(
                graphs,
                scope,
                workspace=workspace,
                editable=self.config.editable,
                lineage_documents=documents,
                semantic_imports=semantic_imports,
                entity_resolution_matches=tuple(
                    match
                    for graph in graphs
                    for match in find_entity_resolution_candidates_for_graph_use_case(graph)
                ),
            )
        else:
            graph = load_graph_use_case(self._single_graph())
            graphs = (graph,)
            workspaces = tuple(
                load_workspace_use_case(name) for name in list_workspaces_use_case()
            )
            payload = browser_graph(
                graph,
                workspaces=workspaces,
                editable=self.config.editable,
                lineage_documents=documents,
                semantic_imports=list_semantic_imports_use_case(graph_name=graph.name),
                entity_resolution_matches=find_entity_resolution_candidates_for_graph_use_case(
                    graph
                ),
            )
        focus_documents = self._focus_documents()
        payload["focuses"] = browser_focus_catalog(
            focus_documents,
            stale_reasons=self._focus_stale_reasons(focus_documents),
        )
        payload["focus_selection"] = (
            self._select_focuses(self.config.focuses) if self.config.focuses else None
        )
        knowledge_documents = list_knowledge_documents_use_case()
        payload["knowledge_documents"] = [
            item.to_dict(include_content=False)
            for item in knowledge_documents
        ]
        graph_by_name = {item.name: item for item in graphs}
        for record in payload["review"]:
            if not isinstance(record, dict):
                continue
            try:
                record_graph = graph_by_name[str(record["graph"])]
                node, _reference = resolve_annotation_target(
                    record_graph,
                    str(record["label"]),
                )
                context = resolve_knowledge(
                    knowledge_documents,
                    record_graph,
                    node,
                    workspace=workspace,
                    mode="scoped",
                )
            except (AnnotationFailure, KeyError, KnowledgeFailure):
                context = KnowledgeContext()
            record["available_context_document_ids"] = [
                item.id for item in context.references
            ]
        return payload

    def mutate(self, route: str, payload: dict[str, Any]) -> dict[str, object]:
        if route == "/api/focus/select":
            return self._select_focuses(_strings(payload, "focuses"))
        if route == "/api/lineage/upstream":
            names = _strings(payload, "lineages") or tuple(self._lineages)
            if not names:
                raise UIFailure("lineage_required", "Select at least one lineage document.")
            trace = trace_upstream_use_case(
                _string(payload, "reference"),
                lineage_names=names,
                graph_names=self._graph_names(),
                max_hops=_integer(payload, "max_hops", default=12, minimum=1, maximum=100),
                states=frozenset(_strings(payload, "states")) or None,
            )
            return trace.to_dict()

        self._require_editable()
        if route == "/api/annotation/edit":
            graph_name = self._check_graph_revision(payload)
            patch = _object(payload, "patch")
            result = edit_annotation_use_case(
                graph_name,
                _string(payload, "reference"),
                patch,
                reason=_string(payload, "reason"),
            )
            return {"record": result.record.to_dict(), "revision": graph_revision(result.graph)}
        if route == "/api/semantic/edit":
            name = _string(payload, "import_name")
            document = load_semantic_import_use_case(name)
            if document.graph_name not in self._graph_names():
                raise UIFailure(
                    "semantic_import_outside_scope",
                    f"Semantic import is outside the UI scope: {name}",
                )
            result = edit_semantic_source_use_case(
                name,
                _string(payload, "target_id"),
                _object(payload, "patch"),
                reason=_string(payload, "reason"),
                expected_revision=_string(payload, "revision"),
            )
            return {
                "import_name": name,
                "revision": result.document.revision,
                "target_id": _string(payload, "target_id"),
            }
        if route == "/api/annotation/decision":
            graph_name = self._check_graph_revision(payload)
            result = decide_annotation_use_case(
                graph_name,
                _string(payload, "reference"),
                state=_string(payload, "state"),
                reason=_string(payload, "reason"),
                include_fields=_boolean(payload, "include_fields", default=False),
            )
            return {
                "records": [record.to_dict() for record in result.records],
                "revision": graph_revision(result.graph),
            }
        if route == "/api/lineage/decision":
            result = decide_lineage_item_use_case(
                _string(payload, "lineage"),
                _string(payload, "item_id"),
                decision=_string(payload, "decision"),
                reason=_string(payload, "reason"),
                expected_revision=_string(payload, "revision"),
            )
            return {
                "item": result.item.to_dict(),
                "revision": lineage_revision(result.document),
            }
        if route == "/api/manual/job":
            name = _string(payload, "lineage")
            result = add_manual_job_use_case(
                name,
                kind=_string(payload, "kind"),
                job_name=_string(payload, "job_name"),
                qualified_name=_string(payload, "qualified_name"),
                language=_string(payload, "language"),
                source_reference=_string(payload, "source_reference"),
                description=_string(payload, "description"),
                expected_revision=_optional_string(payload.get("revision")),
            )
            if name not in self._lineages:
                self._lineages.append(name)
            return {"lineage": browser_lineages((result.document,))[0]}
        if route == "/api/manual/hop":
            name = _string(payload, "lineage")
            result = add_manual_hop_use_case(
                name,
                job_reference=_string(payload, "job"),
                source=_string(payload, "source"),
                target=_string(payload, "target"),
                operation=_string(payload, "operation"),
                role=_string(payload, "role"),
                evidence_reference=_string(payload, "evidence_reference"),
                reason=_string(payload, "reason"),
                line_start=_integer(
                    payload,
                    "line_start",
                    default=1,
                    minimum=1,
                    maximum=1_000_000,
                ),
                line_end=_integer(
                    payload,
                    "line_end",
                    default=1,
                    minimum=1,
                    maximum=1_000_000,
                ),
                expected_revision=_string(payload, "revision"),
            )
            return {
                "item": result.item.to_dict(),
                "revision": lineage_revision(result.document),
            }
        if route == "/api/zone/save":
            return self._save_zone(payload)
        raise UIFailure("route_not_found", "Unknown UI API route.", status=404)

    def _focus_documents(self) -> tuple[FocusDocument, ...]:
        if self._focus_cache is not None:
            return self._focus_cache
        graph_names = set(self._graph_names())
        documents: list[FocusDocument] = []
        for name in list_focuses_use_case():
            document = load_focus_use_case(name)
            required = {item.name for item in document.sources if item.kind == "graph"}
            if required and required <= graph_names:
                documents.append(document)
        self._focus_cache = tuple(documents)
        return self._focus_cache

    def _select_focuses(self, names: tuple[str, ...]) -> dict[str, object]:
        if len(names) != len(set(names)):
            raise UIFailure("duplicate_focus", "Select every focus at most once.")
        allowed = {item.name: item for item in self._focus_documents()}
        outside = sorted(set(names) - set(allowed))
        if outside:
            raise UIFailure(
                "focus_outside_scope",
                f"Focus is outside the UI scope: {', '.join(outside)}",
            )
        documents = tuple(allowed[name] for name in names)
        stale = self._focus_stale_reasons(documents)
        if stale:
            name = sorted(stale)[0]
            raise FocusFailure("focus_stale", stale[name])
        return browser_focus_selection(documents)

    def _focus_stale_reasons(
        self,
        documents: tuple[FocusDocument, ...],
    ) -> dict[str, str]:
        graph_names = sorted(
            {
                item.name
                for document in documents
                for item in document.sources
                if item.kind == "graph"
            }
        )
        lineage_names = sorted(
            {
                item.name
                for document in documents
                for item in document.sources
                if item.kind == "lineage"
            }
        )
        graphs: dict[str, GraphDocument] = {}
        lineages: dict[str, LineageDocument] = {}
        missing: dict[tuple[str, str], str] = {}
        for name in graph_names:
            try:
                graphs[name] = load_graph_use_case(name)
            except GraphFailure:
                missing[("graph", name)] = f"Missing graph source: {name}"
        for name in lineage_names:
            try:
                lineages[name] = load_lineage_use_case(name)
            except LineageFailure:
                missing[("lineage", name)] = f"Missing lineage source: {name}"
        stale: dict[str, str] = {}
        for document in documents:
            missing_reasons = [
                missing[(item.kind, item.name)]
                for item in document.sources
                if (item.kind, item.name) in missing
            ]
            if missing_reasons:
                stale[document.name] = f"Focus {document.name} is stale; {missing_reasons[0]}."
                continue
            try:
                require_current_focus(
                    document,
                    lineages=lineages,
                    graphs=graphs,
                )
            except FocusFailure as exc:
                stale[document.name] = str(exc)
        return stale

    def read(self, route: str) -> dict[str, object]:
        if route == "/api/bootstrap":
            return self.bootstrap()
        raise UIFailure("route_not_found", "Unknown UI API route.", status=404)

    def _require_editable(self) -> None:
        if not self.config.editable:
            raise UIFailure("read_only", "Restart TAREL UI with --edit to change data.", status=403)

    def _check_graph_revision(self, payload: dict[str, Any]) -> str:
        graph_name = self._payload_graph(payload)
        expected = _string(payload, "revision")
        current = graph_revision(load_graph_use_case(graph_name))
        if expected != current:
            raise UIFailure(
                "stale_graph",
                "The graph changed after this page was loaded. Reload before saving.",
                status=409,
            )
        return graph_name

    def _scope(self):
        if not self.config.workspace:
            raise UIFailure("workspace_required", "A workspace is required for scoped UI mode.")
        return resolve_workspace_scope_use_case(
            self.config.workspace,
            systems=self.config.systems,
            graphs=self.config.graphs,
            areas=self.config.areas,
            schemas=self.config.schemas,
            zones=self.config.zones,
        )

    def _graph_names(self) -> tuple[str, ...]:
        if self.config.workspace:
            return self._scope().graph_names
        return (self._single_graph(),)

    def _single_graph(self) -> str:
        if self.config.graph:
            return self.config.graph
        raise UIFailure(
            "ui_source_required",
            "Choose a graph or pass --workspace for a multi-graph view.",
        )

    def _payload_graph(self, payload: dict[str, Any]) -> str:
        value = _optional_string(payload.get("graph"))
        names = self._graph_names()
        if value is None:
            if len(names) == 1:
                return names[0]
            raise UIFailure(
                "graph_required",
                "Choose the graph that owns the object before saving.",
            )
        if value not in names:
            raise UIFailure("graph_outside_scope", f"Graph is outside the UI scope: {value}")
        return value

    def _save_zone(self, payload: dict[str, Any]) -> dict[str, object]:
        workspace_name = _string(payload, "workspace")
        system_name = _string(payload, "system")
        area_name = _string(payload, "area")
        zone_name = _string(payload, "zone")
        description = _optional_string(payload.get("description"))
        members = payload.get("members", [])
        if not isinstance(members, list) or not members:
            raise UIFailure("empty_zone", "A zone requires at least one table or view.")

        existing_names = set(list_workspaces_use_case())
        if workspace_name in existing_names:
            workspace = load_workspace_use_case(workspace_name)
            expected = payload.get("workspace_revision")
            if expected is not None and expected != workspace_revision(workspace):
                raise UIFailure(
                    "stale_workspace",
                    "The workspace changed after this page was loaded. Reload before saving.",
                    status=409,
                )
        else:
            create_workspace_use_case(workspace_name, description="Created by the local TAREL UI.")
            workspace = load_workspace_use_case(workspace_name)

        system = next((item for item in workspace.systems if item.name == system_name), None)
        member_refs = self._zone_member_references(members)
        member_graphs = tuple(sorted({item[0] for item in member_refs}))
        if system is None:
            define_workspace_system_use_case(
                workspace_name,
                system_name,
                graph_names=member_graphs,
                description="Local TAREL UI system.",
            )
        elif not set(member_graphs) <= set(system.graphs):
            define_workspace_system_use_case(
                workspace_name,
                system_name,
                graph_names=tuple(sorted(set(system.graphs) | set(member_graphs))),
                description=system.description,
            )

        workspace = load_workspace_use_case(workspace_name)
        system = next(item for item in workspace.systems if item.name == system_name)
        assigned = {
            (schema.graph, schema.namespace)
            for area in system.areas
            for schema in area.schemas
        }
        required = {
            (graph_name, namespace)
            for graph_name, _label, namespace in member_refs
        }
        unassigned = required - assigned
        if unassigned:
            area = next((item for item in system.areas if item.name == area_name), None)
            references = {
                f"{schema.graph}:{schema.namespace}"
                for schema in area.schemas
            } if area else set()
            references.update(f"{graph_name}:{namespace}" for graph_name, namespace in unassigned)
            define_workspace_area_use_case(
                workspace_name,
                system_name,
                area_name,
                schema_references=tuple(sorted(references)),
                description=area.description if area else "Schemas assigned by the local TAREL UI.",
            )

        result = define_workspace_zone_use_case(
            workspace_name,
            system_name,
            zone_name,
            object_references=tuple(
                f"{graph_name}:{label}" for graph_name, label, _namespace in member_refs
            ),
            description=description,
        )
        return {
            "workspace": result.workspace.to_dict(),
            "workspace_revision": workspace_revision(result.workspace),
        }

    def _zone_member_references(
        self,
        members: list[object],
    ) -> tuple[tuple[str, str, str], ...]:
        references: list[tuple[str, str, str]] = []
        for value in members:
            if isinstance(value, str):
                graph_name = self._payload_graph({})
                reference = value
                object_id = None
            elif isinstance(value, dict):
                graph_name = _string(value, "graph")
                if graph_name not in self._graph_names():
                    raise UIFailure(
                        "graph_outside_scope",
                        f"Graph is outside the UI scope: {graph_name}",
                    )
                reference = _optional_string(value.get("label"))
                object_id = _optional_string(value.get("object_id"))
                if reference is None and object_id is None:
                    raise UIFailure(
                        "invalid_request",
                        "Zone members require label or object_id.",
                    )
            else:
                raise UIFailure("invalid_request", "Zone members must be strings or objects.")

            graph = load_graph_use_case(graph_name)
            matches = [
                node
                for node in graph.nodes
                if node.type in {"table", "view"}
                and (node.id == object_id or node.label == reference)
            ]
            if len(matches) != 1:
                shown = reference or object_id or "unknown"
                raise UIFailure("zone_object_not_found", f"Unknown graph object: {shown}")
            node = matches[0]
            references.append(
                (graph_name, node.label, str(node.metadata.get("namespace") or ""))
            )
        return tuple(sorted(set(references)))


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], backend: TarelUIBackend, token: str) -> None:
        super().__init__(address, _Handler)
        self.backend = backend
        self.token = token


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._api_read(path)
            return
        self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if self.headers.get("X-Tarel-Token") != self.server.token:
            self._json_error(UIFailure("invalid_session", "Invalid UI session token.", status=403))
            return
        if self.headers.get_content_type() != "application/json":
            self._json_error(UIFailure("invalid_content_type", "Expected application/json."))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > _MAX_REQUEST_BYTES:
                raise UIFailure("request_too_large", "UI request is too large.", status=413)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise UIFailure("invalid_request", "UI request root must be an object.")
            self._json(HTTPStatus.OK, self.server.backend.mutate(path, payload))
        except Exception as exc:  # boundary maps known domain failures to JSON
            self._json_error(_ui_failure(exc))

    def log_message(self, format: str, *args: object) -> None:
        return

    def _api_read(self, path: str) -> None:
        try:
            self._json(HTTPStatus.OK, self.server.backend.read(path))
        except Exception as exc:
            self._json_error(_ui_failure(exc))

    def _static(self, path: str) -> None:
        name = "index.html" if path in {"", "/"} else path.removeprefix("/")
        if name not in {"index.html", "app.js", "styles.css", "cytoscape.min.js"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        resource = files("tarel.ui").joinpath("static", name)
        try:
            body = resource.read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if name == "index.html":
            body = body.replace(b"__TAREL_TOKEN__", self.server.token.encode("ascii"))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _STATIC_TYPES[Path(name).suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _content_security_policy())
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, failure: UIFailure) -> None:
        self._json(failure.status, {"error": {"code": failure.code, "message": str(failure)}})

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def run_ui(
    graph: str | None,
    *,
    workspace: str | None = None,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
    lineages: tuple[str, ...] = (),
    focuses: tuple[str, ...] = (),
    editable: bool = False,
    port: int = 0,
    open_browser: bool = True,
) -> int:
    if port < 0 or port > 65535:
        raise UIFailure("invalid_port", "Port must be between 0 and 65535.")
    if bool(graph) == bool(workspace):
        raise UIFailure(
            "ui_source_required",
            "Choose exactly one graph or one --workspace.",
        )
    backend = TarelUIBackend(
        UIConfig(
            graph=graph,
            workspace=workspace,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
            lineages=lineages,
            focuses=focuses,
            editable=editable,
        )
    )
    backend.bootstrap()
    server = _Server(("127.0.0.1", port), backend, secrets.token_urlsafe(32))
    address = f"http://127.0.0.1:{server.server_port}/"
    mode = "edit" if editable else "read-only"
    print(f"TAREL UI ({mode}): {address}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.15, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _ui_failure(exc: Exception) -> UIFailure:
    if isinstance(exc, UIFailure):
        return exc
    if isinstance(
        exc,
        (
            AnnotationFailure,
            EntityResolutionFailure,
            FocusFailure,
            GraphFailure,
            KnowledgeFailure,
            LineageFailure,
            RelationshipFailure,
            SemanticFailure,
            WorkspaceFailure,
        ),
    ):
        code = getattr(exc, "code", "ui_operation_failed")
        status = 409 if code in {
            "focus_stale",
            "stale_graph",
            "stale_lineage",
            "stale_semantic_import",
            "stale_workspace",
        } else 400
        if code.endswith("_not_found"):
            status = 404
        return UIFailure(code, str(exc), status=status)
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
        return UIFailure("invalid_request", "The UI request is invalid.")
    return UIFailure("ui_operation_failed", str(exc), status=500)


def _content_security_policy() -> str:
    return "; ".join((
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ))


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UIFailure("invalid_request", f"UI field must be a non-empty string: {key}")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise UIFailure("invalid_request", "Optional UI field must be a string or null.")
    return value.strip() or None


def _object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise UIFailure("invalid_request", f"UI field must be an object: {key}")
    return value


def _strings(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise UIFailure("invalid_request", f"UI field must be an array of strings: {key}")
    return tuple(item.strip() for item in value)


def _boolean(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise UIFailure("invalid_request", f"UI field must be a boolean: {key}")
    return value


def _integer(
    payload: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise UIFailure("invalid_request", f"UI field is outside its allowed range: {key}")
    return value
