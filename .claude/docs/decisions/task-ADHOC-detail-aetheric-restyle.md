# Task: ADHOC — detail.html "Aetheric Precision" UI restyle

## Meta
- linear_id: none (explicitly no Linear ticket)
- tier: S
- created: 2026-07-18
- status: done

## Brief
Restyle the outer UI shell of src/web/templates/detail.html to the "Aetheric Precision"
minimal light design system (Stitch reference: scratchpad/stitch_detail_full.png).
Canvas visualization logic, data contracts, backend, and Jinja variables unchanged.

## Decision Log
- Fonts: keep system font stack (buildless / zero external requests; app may run on LAN
  without internet). Tone approximated via weights/sizes/letter-spacing. No Google Fonts.
- Meta chips: only variables available in template — state pill (Japanese label map,
  display-only), finished_at, segments_completed. No speaker count / duration (not in Job).
- Transcript: output format is `[start - end] SPEAKER: text` per line → speaker data exists.
  Progressive enhancement: display-only JS parses lines; if >=90% match, render structured
  rows with speaker chip + muted timestamp; otherwise keep styled <pre> fallback.
  Raw `text` contract and server untouched.
- Dark mode: keep color-scheme: light dark; tokens via CSS variables with
  prefers-color-scheme: dark overrides. Canvas ink already derives from body color.
- df-switch: recessed track + white active pill via CSS only; aria-pressed JS untouched.

## Design
Tokens: bg #FAFAFB / card #FFFFFF / text #18181B / muted #71717A / border #EAEAEC /
divider #ECECEE / accent #4F46E5 / success #F0FDF4+#166534. Radius 12px, near-zero
shadows, content width 1040px, body line-height 1.7.

## Implementation Notes
- Single file changed: src/web/templates/detail.html (+254/-59).
- CSS: full token pass (:root vars + prefers-color-scheme dark overrides), 12px radius
  cards, accent-dot h2 labels, pill status/meta chips, score pills, segmented df-switch
  (recessed track + white active pill; JS aria-pressed/display toggling untouched).
- Header: Japanese state label map (display-only), chips for finished_at and
  segments_completed only (variables actually available on Job).
- Transcript: kept <pre class="transcript"> as source + fallback; added display-only
  enhancer script that parses "[start - end] SPEAKER: text" lines (>=90% threshold)
  into rows with avatar letter (first-appearance order), label-caps speaker, muted
  tabular timestamp. No server/data change.
- Dead ".keywords" CSS rules removed (no matching markup).
- Canvas/JS untouched: treemap, force graph, mulberry32, rAF, window.detailTopics,
  decision-flow views (NSKETCH-873).

## Review
- Tier S self-review: PASS.
- Verified via rendered-sample screenshots (jinja2 render harness + agent-browser):
  light/dark × 1280px/390px × decision_flows present/absent — all render correctly,
  hover-only redraw and aria-pressed switcher behavior preserved.
- No tests reference templates; no test impact.

## Deploy
- Branch: feature/detail-aetheric-restyle (commit 9f13724)
- PR: https://github.com/maro525/transcribe/pull/15
