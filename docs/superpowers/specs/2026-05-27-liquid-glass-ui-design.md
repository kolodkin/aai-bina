# Liquid Glass UI redesign

Restyle QueryView's frontend into an Apple "Liquid Glass" look: translucent
frosted surfaces with backdrop blur, lit edges, and soft depth, floating over a
calm dark gradient. Functionality is unchanged — this is purely visual.

## Decisions

- **Style:** Apple Liquid Glass (frosted, translucent, blurred, layered).
- **Theme:** Dark glass.
- **Backdrop:** A single subtle two-tone dark gradient (deep navy → deep
  indigo/violet). No busy multi-blob mesh.
- **Scope:** Whole app — every surface.
- **Motion:** Minimal. Smooth color/border transitions and gentle press
  feedback only; no elaborate animation.

## Approach

Add a thin design-token + glass component-class layer to
`frontend/src/index.css` using Tailwind v4 (`@theme` + `@layer components`),
then apply those classes throughout `frontend/src/App.tsx`. This keeps a single
source of truth for the frosted-glass recipe instead of duplicating long
utility strings across ~25 elements. No new dependencies.

### Tokens (`@theme`)

- Accent: brightened indigo/violet, readable on dark.
- Text: slate-100 primary, slate-400 muted.

### Glass component classes (`@layer components`)

- `.glass-panel` — frosted card: translucent fill, `backdrop-blur-xl`, hairline
  light border, soft drop shadow + inset top highlight (the "lit edge").
- `.glass-input` — translucent inputs/selects/textarea, light text, muted
  placeholder, accent focus ring.
- `.glass-btn` — neutral translucent button.
- `.glass-btn-primary` — vibrant accent gradient button with a subtle sheen.
- `.glass-chip` — pill (e.g. connection status).
- `.is-active` — accent-filled state for toggle buttons (size, db option,
  field, order-by).

## Surfaces

1. `main` — dark gradient backdrop, light text.
2. `QueryView` heading — light with a subtle glow.
3. Prompt input — `.glass-input` in both standalone and in-panel modes.
4. Connection-status pill — `.glass-chip`, keeps emerald dot + "connected -
   <db>" text.
5. Agent toggle + popover — glass button and a floating frosted panel.
6. ClickHouse form — `.glass-panel`; glass inputs; Test = neutral, Connect =
   primary; result text recolored for dark (emerald-300 / red-300).
7. Database picker — glass panel + glass toggle buttons.
8. Query panel — glass panel: predefined select + Save, size buttons, SQL
   textarea (mono), Execute (primary) / Fields / Limit / Offset / Prev / Next /
   Download CSV, the field + order-by picker sub-block (nested translucent),
   order-by chips.
9. Results table — translucent sticky header with blur, light text, subtle
   alternating row tint, hairline borders; tuned for contrast so data stays
   legible.

## Guarantees / non-goals

- Identical behavior; no logic changes.
- Every `data-testid`, `data-ok`, `data-db`, `data-col`, `data-on`, the
  `::new::` option value, the `h1` text "QueryView", the "connected - <db>"
  text, and the results `table`/`thead`/`th` structure are preserved (the e2e
  suite asserts on these).
- No new dependencies; Tailwind v4 only.

## Verification

- `npm run build` (tsc + vite) and `npm run lint` pass.
- Run the app and capture screenshots of each surface via the e2e screenshot
  tooling; review for legibility and the glass effect.
- Full Playwright e2e needs a live ClickHouse, which may be unavailable in the
  container; note it if it cannot run.
