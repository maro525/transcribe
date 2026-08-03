# Current Project: ADHOC — Editable discourse-structure network

## Goal
- Add persistent statement/relation editing to the detail page's discourse-structure network, stacked on the editable word-network branch.

## Key files
- `src/web/templates/detail.html`
- `src/web/app.py`
- `src/artifacts.py`
- `tests/test_artifacts.py`
- `tests/test_artifacts_structure.py`
- `tests/test_web_app.py`

## Architecture
- Keep generated statements, relations, topics, and decision flows immutable; store a revisioned overlay in the structure artifact.
- Add a validated, atomic `PUT /jobs/{filename}/structure-edits` endpoint using the graph-edit security pattern.
- Compose base + edits before building any view; rerun the bounded deterministic `networkScene` layout after mutations.

## Decisions
- User relations are directed (first click source, second target) and default to `elaborates`; memo statements inherit the edited topic.
- Persist user statements/relations, hidden base IDs, and normalized pins; use revision/409 and atomic replacement.
- Do not infer decision-flow semantics from arbitrary relations. Gate 1 decides whether non-network views only note edits or receive explicit relation overlays.
- Create `feature/editable-structure-network` from `feature/editable-word-network`, not main; add no dependencies.
