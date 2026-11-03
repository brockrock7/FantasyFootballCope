# Mobile readability plan

Scope: improve the existing dashboard for phones without removing statistics. The current change adds a wrapping global team control and a scrollable mirrored matchup table; the work below is a proposed follow-up, not a list of already implemented features.

## 1. Navigation and touch controls

- At widths below 700px, put the league title, global team buttons, main tabs, and subtabs on separate rows. Keep the active choice visible when navigating Back/Forward.
- Give team buttons and tabs at least 44px touch targets. Use horizontal scrolling for long tab rows rather than shrinking text or stacking several rows of navigation.
- Label the global control as a selection across charts; keep local team buttons available. Preserve the URL state when changing orientation.

## 2. Tables that preserve context

- Keep horizontal scrolling inside each table, with a visible “Swipe for more columns” hint and a subtle edge shadow. The whole page must not scroll sideways.
- Freeze the team/player column and header in wide standings and roster tables. Limit the frozen column to about 130–150px on phones so numeric columns remain visible.
- Show acronyms with expandable full team names in standings on narrow screens. Wrap column headers to two lines and keep numeric text at least 13px.
- Offer an accessible player detail row for secondary roster statistics; keep player, PPG, mean difference, and SD visible first. Do not silently omit the remaining fields.
- Keep the mirrored matchup table with adjacent opposing points; use a compact photo/name layout and internal scrolling on very narrow screens.

## 3. Charts designed for narrow screens

- Use horizontal bars for league-wide team comparisons on phones. Give each team row enough height to retain a readable acronym and value.
- Keep weekly time-series charts at least 300px tall; limit x-axis ticks to 4–5 and provide complete dates and values in tap tooltips.
- Replace crowded canvas legends with wrapping HTML legend buttons with 44px targets. Persist their series visibility in the existing URL state.
- Keep one radar per row. For dense rank/distribution lines, default to emphasizing the selected team and make other teams available through the same legend controls.
- Show a textual value summary beside pie charts so small slices never require reading tiny in-slice labels.

## 4. Popovers and accessible interaction

- On phones, turn the scoring popover into a bottom sheet with a visible close button, a trapped focus cycle, Escape support, and a chart at least 260px tall. Tapping an SD value must work without hover.
- Pair colors with labels, line styles, or selection outlines. Verify contrast in the lightly shaded blue/orange table.
- Respect reduced motion and retain focus after history navigation.

## Rollout and acceptance checks

Implement navigation and table changes first, charts second, then the scoring sheet. Test at 320, 390, 430, and 768 CSS pixels, portrait and landscape, plus 200% browser zoom. Verify Safari on iOS and Chrome on Android, keyboard access, and a screen-reader pass.

Acceptance: no page-level horizontal overflow; all primary actions have 44px targets; no clipped headers or player names; all statistics remain reachable; selected teams, tabs, sorts, legend visibility, and matchup/week survive reload and native Back/Forward. Compare phone screenshots of Standings, Rosters, Matchup stats, Multiverse, and Trades before and after each phase.
