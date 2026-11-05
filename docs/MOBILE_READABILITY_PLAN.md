# Mobile readability plan

Scope: improve the existing dashboard for phones without removing statistics. The implementation status below accompanies the November 2026 mobile changes. The current restyle retains the original palette and font. Items explicitly marked as follow-up have not been implemented. The previous version is preserved in the [dashboard backup](DASHBOARD_BACKUP.md).

## 1. Navigation and touch controls

- Implemented: at widths up to 700px, the title, global team strip, main tabs, and subtabs occupy separate rows. Primary navigation remains sticky; active clicked or history-restored navigation scrolls into view. Subtabs use a separate pill row, including Trade History / Trade Calculator.
- Implemented: team buttons and tabs have at least 44px touch targets. Long tab rows, local team controls, and the global team strip scroll horizontally.
- Label the global control as a selection across charts; keep local team buttons available. Preserve the URL state when changing orientation.

## 2. Tables that preserve context

- Implemented: all table columns follow intrinsic content widths; hard minimum widths and distributed spare column space are removed. Headers wrap and PPG sits below totals.
- Implemented: tables scroll internally, overflow shows a swipe/scroll hint, and scroll areas are keyboard-focusable. Wide statistics tables keep their identity column and header visible, with an edge rule.
- Implemented: mobile standings use acronyms with full names retained in accessible labels/titles. Roster names wrap at word boundaries and photos shrink to 28px.
- Follow-up: an optional player detail row could move secondary roster statistics out of the horizontal table; currently every column remains available by scrolling.
- Implemented: the mirrored matchup table keeps adjacent opposing points, with compact photos and initial-plus-surname labels on phones. Full names remain in accessible labels/titles.

## 3. Charts designed for narrow screens

- Implemented: every bar chart uses horizontal bars on phones and desktop, including weekly stacks, win/loss differentials, and multiverse distributions. Chart height follows category count, and reference lines and bounds follow the rotated value axis.
- Implemented: standard weekly charts are 320px tall, the live chart is 340px, and the projection comparison is 290px. Phone x-axis ticks are limited to five; complete values remain in tooltips.
- Implemented: wrapping HTML legend buttons have 44px targets and retain the existing URL-backed series/slice visibility. Desktop canvas legends return on wider screens.
- Implemented: radar cards form a swipeable strip on phones. Selected-team weekly position/acquisition cards precede league-wide comparisons. The global selection emphasizes a team in dense rank/distribution lines.
- Follow-up: a dedicated textual pie-value summary could complement the existing acronym legend and tap tooltips.

## 4. Popovers and accessible interaction

- Implemented: explicitly tapping an SD value opens a pinned bottom sheet with a 44px Close control, backdrop, focus containment, Escape support, safe-area padding, and a 250px chart plus touch legend. Hover previews remain nonmodal.
- Implemented: trade history uses solid starter / dashed raw lines, filled start / hollow bench circles, and orange sent / blue received photos. The calculator pairs directional color with labels and explicit selected-player checkboxes.
- Implemented: reduced motion disables chart animation; dismissing a clicked scoring sheet returns focus to its player control.

## Rollout and acceptance checks

Automated Chromium checks exercise every tab/subtab at 320, 390, 430, and 768 CSS pixels, content-based table widths, horizontal bars on phones and desktop, URL history through touch legends, and touch-context scoring-sheet geometry/dismissal/focus. Physical-device Safari/iOS and Chrome/Android checks, 200% browser zoom, and a screen-reader pass remain manual follow-up validation.

Acceptance: no page-level horizontal overflow; all primary actions have 44px targets; no clipped headers or player names; all statistics remain reachable; selected teams, tabs, sorts, legend visibility, and matchup/week survive reload and native Back/Forward. Compare phone screenshots of Standings, Rosters, Matchup stats, Multiverse, and Trades before and after each phase.
