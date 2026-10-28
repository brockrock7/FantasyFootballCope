"""Cross-check the reconstructed scorer against current ESPN roster scores."""
import json

import run_analysis as model

profile = model.load_scoring_settings(refresh=False)
players = model._read_csv(
    model._ensure_asset("players", "players.csv.gz"),
    {"pfr_id", "gsis_id", "espn_id", "display_name"},
)
validation, summary = model.validate_current_espn(profile["scoring_items"], players)
print(json.dumps(summary, indent=2))
