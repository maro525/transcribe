# Current Project: ADHOC — Editable word network planning

## Goal
- Plan an editable post-batch word-network canvas with persistent node/edge changes and pinned layout positions.

## Key files
- `src/web/app.py`
- `src/web/templates/detail.html`
- `src/artifacts.py`
- `tests/test_artifacts.py`
- `tests/test_web_app.py` (planned)

## Architecture
- Generated graph snapshot remains the immutable base.
- A revisioned edit overlay lives in the existing graph artifact and is written atomically.
- FastAPI exposes a validated `PUT /jobs/{filename}/graph-edits` endpoint.
- The existing vanilla JS + Canvas renderer merges the overlay and reheats after edits.

## Decisions
- Use explicit edit mode and two-click edge creation to prevent accidental edits.
- Dragging pins nodes using normalized coordinates; deletion hides generated elements or removes user elements.
- Detect concurrent saves via revision/409; never silently overwrite.
- Add no dependencies; do not touch live graph, treemap, or decision-flow network.
