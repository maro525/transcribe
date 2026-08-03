# Current Project: ADHOC — Editable word network planning

## Goal
- Make the detail page's word-network canvas editable after batch completion, with persistent node/edge edits and pinned positions.

## Key files
- `src/web/templates/detail.html`
- `src/web/app.py`
- `src/artifacts.py`
- `tests/test_artifacts.py`
- `tests/test_web_app.py` (planned)

## Architecture
- Keep the generated `CooccurrenceGraph.snapshot()` as an immutable base and store an optional, revisioned edit overlay in the existing graph artifact.
- Add a validated, atomic `PUT /jobs/{filename}/graph-edits` endpoint.
- Merge base + edits in the existing dependency-free Canvas renderer and reheat the force simulation after mutations.

## Decisions
- Explicit edit mode; two-click edge creation; drag pins a node; visible controls handle deletion and unpinning.
- Persist user nodes/edges, hidden base elements, and normalized pinned positions; detect concurrent edits with a revision and return 409.
- No new dependencies and no changes to live graph, topic treemap, or decision-flow network.
