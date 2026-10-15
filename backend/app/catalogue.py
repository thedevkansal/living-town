"""Print the fixed Cypher catalogue as Markdown (used to generate docs/CYPHER.md)."""
from .queries import CATALOGUE, SCHEMA


def markdown() -> str:
    out = ["# Cypher catalogue", "",
           "Every query Living Town runs, generated from `backend/app/queries.py`. Agents choose a query and its "
           "parameters; they never write Cypher. `read` queries go through `GRAPH.RO_QUERY`.", "",
           "## Schema", "", "```cypher", *SCHEMA, "```", ""]
    for kind, title in (("algo", "Graph algorithms"), ("read", "Reads"), ("write", "Writes")):
        out += [f"## {title}", ""]
        for q in CATALOGUE.values():
            if q.kind == kind:
                out += [f"### `{q.name}`", "", q.purpose, "", "```cypher", q.cypher, "```", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(markdown())
