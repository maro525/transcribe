# Current Project: ADHOC — Editable discourse-structure network

## Goal
- Plan persistent statement/relation editing for the discourse-structure network, reusing the word-network edit foundation.

## Key files
- `src/web/app.py`
- `src/web/templates/detail.html`
- `src/artifacts.py`
- `tests/test_artifacts.py`
- `tests/test_artifacts_structure.py`
- `tests/test_web_app.py`

## Architecture
- Generated structure remains immutable; a revisioned statement/relation overlay is written atomically in its artifact.
- FastAPI exposes a validated `PUT /jobs/{filename}/structure-edits` endpoint using the existing 64 KiB/revision/409 pattern.
- All view models consume composed structure; network mutations rerun the existing bounded deterministic layout rather than adding a permanent rAF loop.

## Decisions
- Use explicit edit mode, directed two-click relation creation, visible deletion, and normalized drag pins.
- Default user relations to `elaborates`; memo statements inherit the active topic and carry no transcript provenance.
- Never derive option/argument/outcome semantics from an arbitrary user relation; Gate 1 controls non-network visualization.
- Branch `feature/editable-structure-network` from `feature/editable-word-network`; add no dependencies.
