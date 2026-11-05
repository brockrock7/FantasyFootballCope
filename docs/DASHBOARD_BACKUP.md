# Dashboard before the mobile restyle

The complete dashboard source and generated page before this redesign are preserved at commit `371d2cc661e76dbc3758b5a4146eed8c9de8077a`, on branch [`codex/dashboard-before-restyle-2026-11-05`](https://github.com/brockrock7/FantasyFootballCope/tree/codex/dashboard-before-restyle-2026-11-05).

A source ZIP, `dashboard-before-restyle-371d2cc.zip`, was also saved in the local repository folder before editing. The ZIP is ignored by Git because the backup branch already preserves its contents remotely.

To inspect the old version without changing the working dashboard, download that branch's source ZIP or check the branch out in a separate worktree. To restore the published dashboard, revert the commit titled **Restyle mobile dashboard and add lineup-aware trade calculator**, then rebuild and publish. A revert preserves live-score snapshots added after the backup. The generator, embedded JavaScript/CSS, guide, and generated page must stay together when reverting; changing only `index.html` would be overwritten by the next scheduled build.
