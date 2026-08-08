"""Pure aggregation of per-graph retrieval results for one workspace scope."""

from __future__ import annotations

from tarel.search import FieldSearchHit, SearchHit, SearchResults
from tarel.workspaces.projection import scoped_node_id, workspace_graph_name
from tarel.workspaces.scope import ResolvedScope


def combine_workspace_search(
    scope: ResolvedScope,
    results: tuple[SearchResults, ...],
    *,
    limit: int,
) -> SearchResults:
    allowed = {(item.graph, item.object_id) for item in scope.objects}
    hits: list[SearchHit] = []
    for result in results:
        graph_name = result.graph
        for hit in result.hits:
            if (graph_name, hit.id) not in allowed:
                continue
            hits.append(
                SearchHit(
                    id=scoped_node_id(graph_name, hit.id),
                    label=f"{graph_name}:{hit.label}",
                    type=hit.type,
                    score=hit.score,
                    matched_terms=hit.matched_terms,
                    reasons=hit.reasons,
                    fields=tuple(
                        FieldSearchHit(
                            id=(
                                scoped_node_id(graph_name, field.id)
                                if field.id
                                else ""
                            ),
                            label=field.label,
                            score=field.score,
                            reasons=field.reasons,
                        )
                        for field in hit.fields
                    ),
                    source_graph=graph_name,
                )
            )
    ranked = tuple(
        sorted(hits, key=lambda item: (-item.score, item.label.casefold(), item.id))[:limit]
    )
    query = results[0].query if results else ""
    mode = results[0].mode if results else "lexical"
    terms = tuple(sorted({term for result in results for term in result.terms}))
    return SearchResults(
        graph=workspace_graph_name(scope.workspace),
        query=query,
        terms=terms,
        hits=ranked,
        mode=mode,
        workspace=scope.workspace,
        graphs=scope.graph_names,
        scope_hash=scope.scope_hash,
    )
