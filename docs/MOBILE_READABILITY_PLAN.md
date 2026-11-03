# Mobile readability plan

Scope: improve the existing dashboard for phones without removing statistics. The implementation status below accompanies the November 2026 mobile changes. Items explicitly marked as follow-up have not been implemented.

## 1. Navigation and touch controls

- Implemented: at widths up to 700px, the title, global team strip, main tabs, and subtabs occupy separate rows. Active clicked or history-restored navigation scrolls into view.
- Implemented: team buttons and tabs have at least 44px touch targets. Long tab rows and the global team strip scroll horizontally.
- Label the global control as a selection across charts; keep local team buttons available. Preserve the URL state when changing orientation.

## 2. Tables that preserve context

- Implemented: all table columns follow intrinsic content widths; hard minimum widths and distributed spare column space are removed. Headers wrap and PPG sits below totals.
- Implemented: tables scroll internally, overflow shows a swipe/scroll hint, and scroll areas are keyboard-focusable. Wide statistics tables keep their identity column and header visible, with an edge rule.
- Implemented: mobile standings use acronyms with full names retained in accessible labels/titles. Roster names wrap at word boundaries and photos shrink to 28px.
- Follow-up: an optional player detail row could move secondary roster statistics out of the horizontal table; currently every column remains available by scrolling.
- Implemented: the mirrored matchup table keeps adjacent opposing points, with compact photos and initial-plus-surname labels on phones. Full names remain in accessible labels/titles.

## 3. Charts designed for narrow screens

- Implemented: position, acquisition, actual/optimal, and starter/bench comparisons use horizontal bars on phones, with chart height proportional to team count. Schedule luck remains horizontal.
- Implemented: standard weekly charts are 320px tall, the live chart is 360px, and the projection comparison is 300px. Phone x-axis ticks are limited to five; complete values remain in tooltips.
- Implemented: wrapping HTML legend buttons have 44px targets and retain the existing URL-backed series/slice visibility. Desktop canvas legends return on wider screens.
- Implemented: one radar per row. The global selection emphasizes a team in dense rank/distribution lines.
- Follow-up: a dedicated textual pie-value summary could complement the existing acronym legend and tap tooltips.

## 4. Popovers and accessible interaction

- Implemented: explicitly tapping an SD value opens a pinned bottom sheet with a 44px Close control, backdrop, focus containment, Escape support, safe-area padding, and a 250px chart plus touch legend. Hover previews remain nonmodal.
- Pair colors with labels, line styles, or selection outlines. Verify contrast in the lightly shaded blue/orange table.
- Implemented: reduced motion disables chart animation; dismissing a clicked scoring sheet returns focus to its player control.

## Rollout and acceptance checks

Automated Chromium checks exercise every tab/subtab at 320, 390, 430, and 768 CSS pixels, content-based table widths, horizontal phone bars and desktop restoration, URL history through touch legends, and touch-context scoring-sheet geometry/dismissal/focus. Physical-device Safari/iOS and Chrome/Android checks, 200% browser zoom, and a screen-reader pass remain manual follow-up validation.

Acceptance: no page-level horizontal overflow; all primary actions have 44px targets; no clipped headers or player names; all statistics remain reachable; selected teams, tabs, sorts, legend visibility, and matchup/week survive reload and native Back/Forward. Compare phone screenshots of Standings, Rosters, Matchup stats, Multiverse, and Trades before and after each phase.
