"""Re-score NFL player-games with this ESPN league's rules and fit score shapes.

Run from the repository root with:
    python analysis/player_score_models/run_analysis.py

Raw source files are cached under this directory and ignored by Git. The script
uses public nflverse releases plus the public ESPN league settings endpoint.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
LEAGUE_ID = 509033444
LEAGUE_SEASON = 2026
TRAIN_LAST_SEASON = 2023
HOLDOUT_FIRST_SEASON = 2024
DATA_FIRST_SEASON = 2015
DATA_LAST_SEASON = 2025
ESPN_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
    f"{LEAGUE_SEASON}/segments/0/leagues/{LEAGUE_ID}?view=mSettings"
)
USER_AGENT = "FantasyFootballDistributionAnalysis/1.0"

SLOT_IDS = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "K": 5, "DEF": 16}
OFFENSE_POSITIONS = {"QB", "RB", "WR", "TE"}
ALL_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]

PLAYER_COLUMNS = {
    "player_id", "player_display_name", "position", "team", "game_id",
    "season", "week", "season_type", "attempts", "completions",
    "passing_yards", "passing_tds", "passing_interceptions",
    "sacks_suffered", "passing_2pt_conversions", "carries", "rushing_yards",
    "rushing_tds", "rushing_2pt_conversions", "receptions", "receiving_yards",
    "receiving_tds", "receiving_2pt_conversions", "fumbles_lost_total",
    "fumble_recovery_tds", "fg_made_0_19", "fg_made_20_29", "fg_made_30_39",
    "fg_made_40_49", "fg_made_50_59", "fg_made_60_", "fg_missed",
    "pat_made", "pat_missed", "pt_inside_10", "pt_inside_20", "pt_yards",
    "pt_att",
}

SNAP_COLUMNS = {
    "game_id", "season", "game_type", "week", "player", "pfr_player_id",
    "position", "team", "offense_snaps", "st_snaps",
}

TEAM_COLUMNS = {
    "season", "week", "team", "season_type", "game_id", "opponent_team",
    "passing_yards", "rushing_yards", "sack_yards_lost", "def_sacks", "def_interceptions",
    "def_fumbles", "def_fumbles_forced", "def_safeties", "def_punt_blocks",
    "def_pat_blocks", "def_fg_blocks", "kickoff_return_yards",
    "punt_return_yards", "fumble_recovery_opp",
}

PBP_COLUMNS = {
    "season", "week", "season_type", "game_id", "play_id", "home_team",
    "away_team", "total_home_score", "total_away_score", "play_type",
    "yards_gained", "rush_touchdown", "pass_touchdown", "rusher_player_id",
    "receiver_player_id", "defteam", "td_team", "return_touchdown",
    "return_team", "return_yards", "kickoff_returner_player_id",
    "punt_returner_player_id", "interception_player_id",
    "fumble_recovery_1_team", "fumble_recovery_1_player_id",
    "two_point_attempt", "two_point_conv_result",
}


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=240) as response, path.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def _ensure_asset(tag: str, filename: str) -> Path:
    path = CACHE / filename
    if not path.exists():
        _download(
            f"https://github.com/nflverse/nflverse-data/releases/download/{tag}/{filename}",
            path,
        )
    return path


def _read_csv(path: Path, wanted: set[str] | None = None) -> pd.DataFrame:
    if wanted is None:
        return pd.read_csv(path, low_memory=False)
    with gzip.open(path, "rt", newline="", encoding="utf-8") as stream:
        header = next(stream).rstrip("\r\n").split(",")
    columns = [column for column in header if column in wanted]
    return pd.read_csv(path, usecols=columns, low_memory=False)


def load_scoring_settings(refresh: bool) -> dict:
    path = HERE / "scoring_settings.json"
    if not refresh and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    request = urllib.request.Request(ESPN_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    settings = data.get("settings", {})
    scoring = settings.get("scoringSettings", {})
    profile = {
        "source": "Public ESPN mSettings endpoint",
        "season": LEAGUE_SEASON,
        "league_id": LEAGUE_ID,
        "player_rank_type": scoring.get("playerRankType"),
        "scoring_items": scoring.get("scoringItems", []),
    }
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return profile


def _number(value) -> float:
    if value is None or pd.isna(value):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _id_string(value) -> str:
    """Normalize identifiers that CSV readers may otherwise format as floats."""
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (int, float, np.integer, np.floating)):
        number = float(value)
        if np.isfinite(number) and number.is_integer():
            return str(int(number))
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def _truthy(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return pd.to_numeric(series, errors="coerce").fillna(0).ne(0)


def _clean_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", value.lower())


def download_inputs(years: range) -> None:
    _ensure_asset("players", "players.csv.gz")
    for year in years:
        _ensure_asset("stats_player", f"stats_player_week_{year}.csv.gz")
        _ensure_asset("snap_counts", f"snap_counts_{year}.csv.gz")
        _ensure_asset("stats_team", f"stats_team_week_{year}.csv.gz")
        _ensure_asset("pbp", f"play_by_play_{year}.csv.gz")


def _load_pbp_season(year: int) -> pd.DataFrame:
    path = _ensure_asset("pbp", f"play_by_play_{year}.csv.gz")
    frame = _read_csv(path, PBP_COLUMNS)
    if "season_type" in frame:
        frame = frame[frame["season_type"].eq("REG")].copy()
    return frame


def _long_td_features(pbp: pd.DataFrame) -> pd.DataFrame:
    columns = ["game_id", "player_id", "rush40", "rush50", "rec40", "rec50"]
    chunks = []
    yards = pd.to_numeric(pbp.get("yards_gained"), errors="coerce").fillna(0)
    rush_td = _truthy(pbp.get("rush_touchdown", pd.Series(index=pbp.index, dtype=float)))
    pass_td = _truthy(pbp.get("pass_touchdown", pd.Series(index=pbp.index, dtype=float)))
    rush = pbp.loc[rush_td & yards.ge(40), ["game_id", "rusher_player_id"]].copy()
    if not rush.empty:
        rush = rush.rename(columns={"rusher_player_id": "player_id"})
        rush["rush40"] = 1
        rush["rush50"] = (yards.loc[rush.index] >= 50).astype(int)
        chunks.append(rush.groupby(["game_id", "player_id"], as_index=False)[["rush40", "rush50"]].sum())
    rec = pbp.loc[pass_td & yards.ge(40), ["game_id", "receiver_player_id"]].copy()
    if not rec.empty:
        rec = rec.rename(columns={"receiver_player_id": "player_id"})
        rec["rec40"] = 1
        rec["rec50"] = (yards.loc[rec.index] >= 50).astype(int)
        chunks.append(rec.groupby(["game_id", "player_id"], as_index=False)[["rec40", "rec50"]].sum())
    if not chunks:
        return pd.DataFrame(columns=columns)
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged.merge(chunk, on=["game_id", "player_id"], how="outer")
    for column in columns[2:]:
        if column not in merged:
            merged[column] = 0
        merged[column] = merged[column].fillna(0)
    return merged[columns]


def _defense_event_features(pbp: pd.DataFrame) -> pd.DataFrame:
    """Count return touchdowns by the team credited with the score."""
    output = []
    if pbp.empty:
        return pd.DataFrame(columns=["game_id", "team", "kr_td", "pr_td", "int_td", "fum_td", "blocked_td", "def_2pt_return", "one_pt_safety"])
    td_team = pbp.get("td_team", pd.Series(index=pbp.index, dtype=object)).fillna("").astype(str)
    return_td = _truthy(pbp.get("return_touchdown", pd.Series(index=pbp.index, dtype=float)))
    play_type = pbp.get("play_type", pd.Series(index=pbp.index, dtype=object)).fillna("").astype(str)
    int_player = pbp.get("interception_player_id", pd.Series(index=pbp.index, dtype=object)).fillna("").astype(str)
    fumble_team = pbp.get("fumble_recovery_1_team", pd.Series(index=pbp.index, dtype=object)).fillna("").astype(str)
    kick_returner = pbp.get("kickoff_returner_player_id", pd.Series(index=pbp.index, dtype=object)).fillna("").astype(str)
    punt_returner = pbp.get("punt_returner_player_id", pd.Series(index=pbp.index, dtype=object)).fillna("").astype(str)
    has_td = return_td & td_team.ne("")
    fields = {
        "kr_td": has_td & kick_returner.ne(""),
        "pr_td": has_td & punt_returner.ne(""),
        "int_td": has_td & int_player.ne(""),
        "fum_td": has_td & fumble_team.eq(td_team),
        "blocked_td": has_td & play_type.isin(["field_goal", "extra_point"]),
    }
    for name, mask in fields.items():
        subset = pbp.loc[mask, ["game_id"]].copy()
        subset["team"] = td_team.loc[mask].values
        subset[name] = 1
        output.append(subset.groupby(["game_id", "team"], as_index=False)[name].sum())
    conversion = pbp.get("two_point_attempt", pd.Series(index=pbp.index, dtype=float))
    conv_result = pbp.get("two_point_conv_result", pd.Series(index=pbp.index, dtype=object)).fillna("").astype(str).str.lower()
    conv_mask = _truthy(conversion) & conv_result.isin(["defense", "defensive", "return"])
    if conv_mask.any():
        subset = pbp.loc[conv_mask, ["game_id"]].copy()
        subset["team"] = pbp.loc[conv_mask, "defteam"].fillna("").values
        subset["def_2pt_return"] = 1
        output.append(subset.groupby(["game_id", "team"], as_index=False)["def_2pt_return"].sum())
    if not output:
        return pd.DataFrame(columns=["game_id", "team"])
    merged = output[0]
    for chunk in output[1:]:
        merged = merged.merge(chunk, on=["game_id", "team"], how="outer")
    for name in ["kr_td", "pr_td", "int_td", "fum_td", "blocked_td", "def_2pt_return", "one_pt_safety"]:
        if name not in merged:
            merged[name] = 0
        merged[name] = merged[name].fillna(0)
    return merged


def _game_scores(pbp: pd.DataFrame) -> dict[tuple[str, str], float]:
    needed = {"game_id", "home_team", "away_team", "total_home_score", "total_away_score"}
    if not needed.issubset(pbp.columns):
        return {}
    frame = pbp[list(needed)].copy()
    frame["total_home_score"] = pd.to_numeric(frame["total_home_score"], errors="coerce")
    frame["total_away_score"] = pd.to_numeric(frame["total_away_score"], errors="coerce")
    result = {}
    # The running scores are monotone; max is robust to trailing no-play rows.
    for game_id, group in frame.groupby("game_id", sort=False):
        row = group.iloc[-1]
        home, away = str(row["home_team"]), str(row["away_team"])
        home_score = group["total_home_score"].max()
        away_score = group["total_away_score"].max()
        if pd.notna(home_score):
            result[(str(game_id), home)] = float(home_score)
        if pd.notna(away_score):
            result[(str(game_id), away)] = float(away_score)
    return result


def _item_points(item: dict, slot_id: int) -> float:
    overrides = item.get("pointsOverrides") or {}
    value = overrides.get(str(slot_id), item.get("points", 0))
    return _number(value)


def _score_from_items(values: dict[int, float], items: list[dict], slot_id: int) -> float:
    total = 0.0
    for item in items:
        total += _number(values.get(int(item["statId"]), 0)) * _item_points(item, slot_id)
    return total


def _player_stat_values(row: pd.Series, long_td: dict) -> dict[int, float]:
    value = lambda key: _number(row.get(key, 0))
    rushing_yards = value("rushing_yards")
    receiving_yards = value("receiving_yards")
    return {
        3: value("passing_yards"),
        4: value("passing_tds"),
        19: value("passing_2pt_conversions"),
        20: value("passing_interceptions"),
        24: rushing_yards,
        25: value("rushing_tds"),
        26: value("rushing_2pt_conversions"),
        35: _number(long_td.get("rush40", 0)),
        36: _number(long_td.get("rush50", 0)),
        37: float(100 <= rushing_yards < 200),
        38: float(rushing_yards >= 200),
        42: receiving_yards,
        43: value("receiving_tds"),
        44: value("receiving_2pt_conversions"),
        45: _number(long_td.get("rec40", 0)),
        46: _number(long_td.get("rec50", 0)),
        53: value("receptions"),
        56: float(100 <= receiving_yards < 200),
        57: float(receiving_yards >= 200),
        63: value("fumble_recovery_tds"),
        64: value("sacks_suffered"),
        72: value("fumbles_lost_total"),
        77: value("fg_made_40_49"),
        80: value("fg_made_0_19") + value("fg_made_20_29") + value("fg_made_30_39"),
        85: value("fg_missed"),
        86: value("pat_made"),
        88: value("pat_missed"),
        140: value("pt_inside_10"),
        141: value("pt_inside_20"),
        150: float(40 <= value("punt_average") < 42),
        198: value("fg_made_50_59"),
        201: value("fg_made_60_"),
    }


def _build_offense_games(years: range, items: list[dict], players: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    crosswalk = players[["pfr_id", "gsis_id", "espn_id", "display_name"]].copy()
    crosswalk = crosswalk.dropna(subset=["pfr_id"]).drop_duplicates("pfr_id")
    pfr_to_gsis = dict(zip(crosswalk["pfr_id"].astype(str), crosswalk["gsis_id"].fillna("").astype(str)))
    espn_by_gsis = {
        str(gsis): _id_string(espn_id)
        for gsis, espn_id in zip(players["gsis_id"].fillna("").astype(str), players["espn_id"])
    }
    rows = []
    stats_columns = PLAYER_COLUMNS
    stats_aliases = {"JAC": "JAX", "LAR": "LA"}
    for year in years:
        snap_path = _ensure_asset("snap_counts", f"snap_counts_{year}.csv.gz")
        stats_path = _ensure_asset("stats_player", f"stats_player_week_{year}.csv.gz")
        snaps = _read_csv(snap_path, SNAP_COLUMNS)
        stats = _read_csv(stats_path, stats_columns)
        snaps = snaps[snaps["game_type"].eq("REG")].copy()
        snaps["position"] = snaps["position"].replace({"FB": "RB"})
        offensive = snaps["position"].isin(OFFENSE_POSITIONS) & pd.to_numeric(snaps["offense_snaps"], errors="coerce").fillna(0).gt(0)
        kicker = snaps["position"].eq("K") & pd.to_numeric(snaps["st_snaps"], errors="coerce").fillna(0).gt(0)
        snaps = snaps[offensive | kicker].copy()
        snaps["gsis_id"] = snaps["pfr_player_id"].astype(str).map(pfr_to_gsis).fillna("")
        snaps["name_key"] = snaps["player"].map(_clean_name)
        snaps["team_key"] = snaps["team"].replace(stats_aliases)
        stats = stats[stats["season_type"].eq("REG")].copy()
        stats["player_id"] = stats["player_id"].fillna("").astype(str)
        stats["name_key"] = stats["player_display_name"].map(_clean_name)
        stats["team_key"] = stats["team"].replace(stats_aliases)
        stats = stats.drop_duplicates(["game_id", "player_id"])
        stats = stats.rename(columns={
            "season": "season_stats", "week": "week_stats", "team": "team_stats", "position": "position_stats",
        })
        mapped = snaps[snaps["gsis_id"].ne("")].merge(
            stats, left_on=["game_id", "gsis_id"], right_on=["game_id", "player_id"], how="left", suffixes=("_snap", "_stats")
        )
        unmatched = snaps[snaps["gsis_id"].eq("")].merge(
            stats, left_on=["game_id", "team_key", "name_key"], right_on=["game_id", "team_key", "name_key"], how="left", suffixes=("_snap", "_stats")
        )
        merged = pd.concat([mapped, unmatched], ignore_index=True, sort=False)
        for column in stats_columns:
            if column in merged:
                merged[column] = merged[column].fillna(0)
        if "player_id" in merged:
            merged["gsis_id"] = merged["gsis_id"].where(
                merged["gsis_id"].astype(str).ne(""), merged["player_id"].fillna("").astype(str)
            )
        merged["espn_id"] = merged["gsis_id"].astype(str).map(espn_by_gsis).fillna("")
        # A player with snaps but no player-stat row has a true zero for this game.
        pbp = _load_pbp_season(year)
        long = _long_td_features(pbp)
        if not long.empty:
            long["player_id"] = long["player_id"].fillna("").astype(str)
            merged = merged.merge(long, left_on=["game_id", "gsis_id"], right_on=["game_id", "player_id"], how="left", suffixes=("", "_long"))
            for column in ["rush40", "rush50", "rec40", "rec50"]:
                merged[column] = merged[column].fillna(0)
        else:
            for column in ["rush40", "rush50", "rec40", "rec50"]:
                merged[column] = 0
        merged["model_position"] = merged["position"].replace({"FB": "RB"})
        scores = []
        for _, row in merged.iterrows():
            pos = row["model_position"]
            slot = SLOT_IDS.get(pos)
            if slot is None:
                scores.append(np.nan)
                continue
            values = _player_stat_values(row, row)
            scores.append(_score_from_items(values, items, slot))
        merged["points"] = scores
        # Player-stat-only scoring rows are omitted if the snap feed has no player;
        # the active-game sample intentionally uses the snap feed as its denominator.
        merged["player_key"] = merged["gsis_id"].where(merged["gsis_id"].astype(str).ne(""), merged["name_key"])
        merged["position"] = merged["model_position"]
        merged["player_name"] = merged.get("player_snap", merged.get("player", ""))
        if isinstance(merged["player_name"], str):
            merged["player_name"] = ""
        else:
            merged["player_name"] = merged["player_name"].fillna(merged.get("player_display_name", ""))
        snap_field = np.where(merged["position"].eq("K"), merged["st_snaps"], merged["offense_snaps"])
        merged["snaps"] = pd.to_numeric(pd.Series(snap_field, index=merged.index), errors="coerce").fillna(0)
        rows.append(merged[["season", "week", "game_id", "team", "espn_id", "player_key", "player_name", "position", "snaps", "points"]])
    output = pd.concat(rows, ignore_index=True)
    output = output.dropna(subset=["points"])
    output = output.drop_duplicates(["season", "game_id", "player_key"])
    coverage = {
        "active_offense_and_k_games": int(len(output)),
        "unmatched_player_id_games": int(output["player_key"].astype(str).str.len().eq(0).sum()),
        "positions": {str(k): int(v) for k, v in output["position"].value_counts().to_dict().items()},
    }
    return output, coverage


def _build_defense_games(years: range, items: list[dict]) -> pd.DataFrame:
    rows = []
    for year in years:
        team_path = _ensure_asset("stats_team", f"stats_team_week_{year}.csv.gz")
        teams = _read_csv(team_path, TEAM_COLUMNS)
        teams = teams[teams["season_type"].eq("REG")].copy()
        teams = teams.drop_duplicates(["game_id", "team"])
        teams["yards_for"] = (
            pd.to_numeric(teams["passing_yards"], errors="coerce").fillna(0)
            + pd.to_numeric(teams["rushing_yards"], errors="coerce").fillna(0)
            + pd.to_numeric(teams["sack_yards_lost"], errors="coerce").fillna(0)
        )
        opp_yards = teams.set_index(["game_id", "team"])["yards_for"].to_dict()
        pbp = _load_pbp_season(year)
        score_map = _game_scores(pbp)
        def_events = _defense_event_features(pbp)
        event_map = {}
        if not def_events.empty:
            event_map = def_events.set_index(["game_id", "team"]).to_dict(orient="index")
        year_rows = []
        for _, row in teams.iterrows():
            team = str(row["team"])
            opponent = str(row["opponent_team"])
            points_allowed = score_map.get((str(row["game_id"]), opponent), 0.0)
            yards_allowed = _number(opp_yards.get((str(row["game_id"]), opponent), 0))
            values = {
                89: float(points_allowed == 0),
                90: float(1 <= points_allowed <= 6),
                91: float(7 <= points_allowed <= 13),
                92: float(14 <= points_allowed <= 17),
                121: float(18 <= points_allowed <= 21),
                122: float(22 <= points_allowed <= 27),
                123: float(28 <= points_allowed <= 34),
                124: float(35 <= points_allowed <= 45),
                125: float(points_allowed >= 46),
                128: float(yards_allowed < 100),
                129: float(100 <= yards_allowed < 200),
                130: float(200 <= yards_allowed < 300),
                131: float(300 <= yards_allowed < 350),
                132: float(350 <= yards_allowed < 400),
                133: float(400 <= yards_allowed < 450),
                134: float(450 <= yards_allowed < 500),
                135: float(500 <= yards_allowed < 550),
                136: float(yards_allowed >= 550),
                95: _number(row.get("def_interceptions")),
                96: _number(row.get("fumble_recovery_opp")),
                97: sum(_number(row.get(col)) for col in ["def_punt_blocks", "def_pat_blocks", "def_fg_blocks"]),
                98: _number(row.get("def_safeties")),
                99: _number(row.get("def_sacks")),
                106: _number(row.get("def_fumbles_forced")),
                114: _number(row.get("kickoff_return_yards")),
                115: _number(row.get("punt_return_yards")),
            }
            values.update(event_map.get((str(row["game_id"]), team), {}))
            # Map the extracted return-event names to ESPN's stat category ids.
            values[93] = _number(values.pop("blocked_td", 0))
            values[101] = _number(values.pop("kr_td", 0))
            values[102] = _number(values.pop("pr_td", 0))
            values[103] = _number(values.pop("int_td", 0))
            values[104] = _number(values.pop("fum_td", 0))
            values[206] = _number(values.pop("def_2pt_return", 0))
            values[209] = _number(values.pop("one_pt_safety", 0))
            score = _score_from_items(values, items, SLOT_IDS["DEF"])
            year_rows.append({
                "season": int(row["season"]), "week": int(row["week"]), "game_id": str(row["game_id"]),
                "team": team, "espn_id": "", "player_key": f"DEF:{team}", "player_name": f"{team} D/ST",
                "position": "DEF", "snaps": 1, "points": score,
            })
        rows.extend(year_rows)
    return pd.DataFrame(rows)


def build_scored_games(years: range, items: list[dict]) -> tuple[pd.DataFrame, dict]:
    players_path = _ensure_asset("players", "players.csv.gz")
    players = _read_csv(players_path, {"pfr_id", "gsis_id", "espn_id", "display_name"})
    offense, coverage = _build_offense_games(years, items, players)
    defense = _build_defense_games(years, items)
    output = pd.concat([offense, defense], ignore_index=True)
    output["season"] = pd.to_numeric(output["season"], errors="coerce").astype(int)
    output["week"] = pd.to_numeric(output["week"], errors="coerce").astype(int)
    output["points"] = pd.to_numeric(output["points"], errors="coerce").fillna(0.0)
    output = output.sort_values(["season", "week", "position", "player_key"]).reset_index(drop=True)
    coverage["defense_games"] = int(len(defense))
    coverage["total_games"] = int(len(output))
    return output, coverage


def _normal_cdf(values) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    return np.array([0.5 * (1 + math.erf(float(v) / math.sqrt(2))) for v in x.ravel()]).reshape(x.shape)


def _digamma(x: float) -> float:
    result = 0.0
    while x < 8.0:
        result -= 1.0 / x
        x += 1.0
    inv = 1.0 / x
    inv2 = inv * inv
    return result + math.log(x) - 0.5 * inv - inv2 * (1 / 12 - inv2 * (1 / 120 - inv2 * (1 / 252)))


def _gamma_shape_mle(x: np.ndarray) -> float:
    mean = float(x.mean())
    s = math.log(mean) - float(np.log(x).mean())
    if s <= 1e-12:
        return 1e6
    lo, hi = 1e-4, 1e6
    for _ in range(100):
        mid = math.sqrt(lo * hi)
        value = math.log(mid) - _digamma(mid)
        if value > s:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def _lognormal_mixture_fit(x: np.ndarray, seed: int = 7) -> dict:
    z = np.log(np.asarray(x, dtype=float))
    overall_var = max(float(z.var()), 1e-4)
    best = None
    quantile_starts = [(.2, .8), (.3, .7), (.1, .9), (.4, .6)]
    for q1, q2 in quantile_starts:
        means = np.array([np.quantile(z, q1), np.quantile(z, q2)], dtype=float)
        variances = np.array([overall_var, overall_var], dtype=float)
        weights = np.array([0.5, 0.5], dtype=float)
        for _ in range(300):
            log_component = np.column_stack([
                np.log(max(weights[j], 1e-12))
                - 0.5 * np.log(2 * math.pi * variances[j])
                - 0.5 * (z - means[j]) ** 2 / variances[j]
                for j in range(2)
            ])
            row_max = log_component.max(axis=1, keepdims=True)
            probabilities = np.exp(log_component - row_max)
            responsibilities = probabilities / probabilities.sum(axis=1, keepdims=True)
            counts = responsibilities.sum(axis=0)
            new_weights = counts / len(z)
            new_means = (responsibilities * z[:, None]).sum(axis=0) / np.maximum(counts, 1e-9)
            new_variances = (responsibilities * (z[:, None] - new_means) ** 2).sum(axis=0) / np.maximum(counts, 1e-9)
            new_variances = np.maximum(new_variances, 0.015 ** 2)
            if np.max(np.abs(new_means - means)) < 1e-8 and np.max(np.abs(new_weights - weights)) < 1e-8:
                weights, means, variances = new_weights, new_means, new_variances
                break
            weights, means, variances = new_weights, new_means, new_variances
        order = np.argsort(means)
        weights, means, variances = weights[order], means[order], variances[order]
        params = {"weights": weights, "means": means, "scales": np.sqrt(variances)}
        ll = float(_logpdf_lognorm_mix(x, params).sum())
        if best is None or ll > best[0]:
            best = (ll, params)
    return best[1]


def _logpdf_lognorm_mix(x: np.ndarray, params: dict) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    z = np.log(x)
    cols = []
    for weight, mean, scale in zip(params["weights"], params["means"], params["scales"]):
        cols.append(math.log(max(float(weight), 1e-12)) - math.log(float(scale)) - 0.5 * math.log(2 * math.pi)
                    - 0.5 * ((z - float(mean)) / float(scale)) ** 2 - z)
    a, b = cols
    maxv = np.maximum(a, b)
    return maxv + np.log(np.exp(a - maxv) + np.exp(b - maxv))


def _fit_positive_models(x: np.ndarray) -> dict:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x) & (x > 0)]
    mu = float(x.mean())
    sigma = max(float(x.std(ddof=0)), 1e-8)
    normal_z0 = _normal_cdf(np.array([mu / sigma]))[0]
    normal_params = {"mean": mu, "scale": sigma, "positive_mass": float(normal_z0)}
    logs = np.log(x)
    lognormal_params = {"mean_log": float(logs.mean()), "scale_log": max(float(logs.std(ddof=0)), 1e-8)}
    shape = _gamma_shape_mle(x)
    gamma_params = {"shape": shape, "scale": mu / shape}
    mixture_params = _lognormal_mixture_fit(x)
    return {
        "truncated_normal": normal_params,
        "gamma": gamma_params,
        "lognormal": lognormal_params,
        "two_lognormal": mixture_params,
    }


def _logpdf_model(x: np.ndarray, name: str, params: dict) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if name == "truncated_normal":
        mu, sigma = params["mean"], params["scale"]
        z0 = float(_normal_cdf(np.array([mu / sigma]))[0])
        return -0.5 * ((x - mu) / sigma) ** 2 - math.log(sigma) - 0.5 * math.log(2 * math.pi) - math.log(max(z0, 1e-12))
    if name == "gamma":
        k, scale = params["shape"], params["scale"]
        return (k - 1) * np.log(x) - x / scale - math.lgamma(k) - k * math.log(scale)
    if name == "lognormal":
        mu, sigma = params["mean_log"], params["scale_log"]
        z = np.log(x)
        return -0.5 * ((z - mu) / sigma) ** 2 - np.log(x * sigma) - 0.5 * math.log(2 * math.pi)
    if name == "two_lognormal":
        return _logpdf_lognorm_mix(x, params)
    raise KeyError(name)


def _regularized_gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma, Numerical Recipes series/continued fraction."""
    if x <= 0:
        return 0.0
    if a <= 0:
        return float("nan")
    gln = math.lgamma(a)
    eps, tiny = 1e-14, 1e-300
    if x < a + 1.0:
        ap = a
        total = term = 1.0 / a
        for _ in range(10000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * eps:
                break
        return min(1.0, max(0.0, total * math.exp(-x + a * math.log(x) - gln)))
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / max(b, tiny)
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    q = math.exp(-x + a * math.log(x) - gln) * h
    return min(1.0, max(0.0, 1.0 - q))


def _cdf_model(value: float, name: str, params: dict) -> float:
    if value <= 0:
        return 0.0
    if name == "truncated_normal":
        mu, sigma = params["mean"], params["scale"]
        base = float(_normal_cdf(np.array([(value - mu) / sigma]))[0])
        below0 = float(_normal_cdf(np.array([-mu / sigma]))[0])
        return min(1.0, max(0.0, (base - below0) / max(1 - below0, 1e-12)))
    if name == "gamma":
        return _regularized_gamma_p(params["shape"], value / params["scale"])
    if name == "lognormal":
        z = (math.log(value) - params["mean_log"]) / params["scale_log"]
        return float(_normal_cdf(np.array([z]))[0])
    if name == "two_lognormal":
        total = 0.0
        for weight, mean, scale in zip(params["weights"], params["means"], params["scales"]):
            z = (math.log(value) - float(mean)) / float(scale)
            total += float(weight) * float(_normal_cdf(np.array([z]))[0])
        return min(1.0, max(0.0, total))
    raise KeyError(name)


def _fit_and_compare(games: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    games = games.copy()
    grouped = games.groupby(["position", "player_key", "season"], dropna=False)
    season_rows = []
    eligible = set()
    for (position, player_key, season), group in grouped:
        scores = pd.to_numeric(group["points"], errors="coerce").dropna().to_numpy(dtype=float)
        positive = scores[scores > 0]
        if len(scores) >= 8 and len(positive) >= 3 and positive.mean() > 0:
            eligible.add((position, player_key, int(season)))
            season_rows.append({
                "position": position, "player_key": player_key, "season": int(season),
                "games": len(scores), "mean": float(scores.mean()), "sd": float(scores.std(ddof=0)),
                "positive_mean": float(positive.mean()), "zero_rate": float(np.mean(scores == 0)),
                "negative_rate": float(np.mean(scores < 0)),
                "normal_p_negative": float(_normal_cdf(np.array([-scores.mean() / max(scores.std(ddof=0), 1e-8)]))[0]),
                "normal_p_under_1": float(_normal_cdf(np.array([(1 - scores.mean()) / max(scores.std(ddof=0), 1e-8)]))[0]),
                "observed_under_1": float(np.mean(scores < 1)),
                "observed_negative": float(np.mean(scores < 0)),
            })
    season_frame = pd.DataFrame(season_rows)
    eligible_games = games.merge(season_frame[["position", "player_key", "season", "positive_mean"]],
                                 on=["position", "player_key", "season"], how="inner")
    positive_games = eligible_games[eligible_games["points"] > 0].copy()
    positive_games["relative_score"] = positive_games["points"] / positive_games["positive_mean"]
    model_rows = []
    params_out = {}
    for position in ALL_POSITIONS:
        pos_train = positive_games[(positive_games.position == position) & (positive_games.season <= TRAIN_LAST_SEASON)]
        pos_test = positive_games[(positive_games.position == position) & (positive_games.season >= HOLDOUT_FIRST_SEASON)]
        x_train = pos_train["relative_score"].to_numpy(dtype=float)
        x_test = pos_test["relative_score"].to_numpy(dtype=float)
        if len(x_train) < 100 or len(x_test) < 20:
            continue
        fitted = _fit_positive_models(x_train)
        params_out[position] = {}
        for name, params in fitted.items():
            train_log = _logpdf_model(x_train, name, params)
            test_log = _logpdf_model(x_test, name, params)
            n_params = {"truncated_normal": 2, "gamma": 2, "lognormal": 2, "two_lognormal": 5}[name]
            bic = -2 * float(train_log.sum()) + n_params * math.log(len(x_train))
            model_rows.append({
                "position": position, "model": name,
                "train_positive_games": len(x_train), "test_positive_games": len(x_test),
                "train_mean_log_score": float(train_log.mean()),
                "test_mean_log_score": float(test_log.mean()),
                "train_bic": bic,
                "test_p_under_0_25_of_positive_mean": _cdf_model(0.25, name, params),
                "test_observed_fraction_under_0_25": float(np.mean(x_test <= 0.25)),
            })
            params_out[position][name] = {
                k: (v.tolist() if isinstance(v, np.ndarray) else float(v) if isinstance(v, (np.floating, np.integer)) else v)
                for k, v in params.items()
            }
    model_frame = pd.DataFrame(model_rows)
    empirical_rows = []
    for (position, split), group in games.assign(split=np.where(games.season <= TRAIN_LAST_SEASON, "train", "holdout")).groupby(["position", "split"]):
        scores = group["points"].to_numpy(dtype=float)
        empirical_rows.append({
            "position": position, "split": split, "games": len(scores),
            "zero_rate": float(np.mean(scores == 0)),
            "negative_rate": float(np.mean(scores < 0)),
            "under_1_rate": float(np.mean(scores < 1)),
            "under_5_rate": float(np.mean(scores < 5)),
            "mean": float(scores.mean()), "sd": float(scores.std(ddof=1)),
            "q05": float(np.quantile(scores, .05)), "q10": float(np.quantile(scores, .10)),
            "q25": float(np.quantile(scores, .25)), "median": float(np.quantile(scores, .50)),
            "q75": float(np.quantile(scores, .75)), "q90": float(np.quantile(scores, .90)),
            "q95": float(np.quantile(scores, .95)),
        })
    empirical = pd.DataFrame(empirical_rows)
    normal_rows = []
    for (position, split), group in season_frame.assign(split=np.where(season_frame.season <= TRAIN_LAST_SEASON, "train", "holdout")).groupby(["position", "split"]):
        n = group["games"].sum()
        normal_rows.append({
            "position": position, "split": split, "player_seasons": len(group),
            "active_games": int(n),
            "observed_negative_rate": float(np.average(group["observed_negative"], weights=group["games"])),
            "normal_predicted_negative_rate": float(np.average(group["normal_p_negative"], weights=group["games"])),
            "observed_under_1_rate": float(np.average(group["observed_under_1"], weights=group["games"])),
            "normal_predicted_under_1_rate": float(np.average(group["normal_p_under_1"], weights=group["games"])),
            "observed_zero_rate": float(np.average(group["zero_rate"], weights=group["games"])),
        })
    normal_frame = pd.DataFrame(normal_rows)
    for position in ALL_POSITIONS:
        scores = games.loc[
            games["position"].eq(position) & games["season"].le(TRAIN_LAST_SEASON), "points"
        ].to_numpy(dtype=float)
        negative = pd.Series(scores[scores < 0]).round(2).value_counts().sort_index()
        params_out.setdefault(position, {})["outcome_masses"] = {
            "training_games": int(len(scores)),
            "p_negative": float(np.mean(scores < 0)) if len(scores) else 0.0,
            "p_zero": float(np.mean(scores == 0)) if len(scores) else 0.0,
            "p_positive": float(np.mean(scores > 0)) if len(scores) else 0.0,
            "negative_score_pmf": {
                f"{score:.2f}": float(count / negative.sum())
                for score, count in negative.items()
            } if len(negative) else {},
        }
    return model_frame, empirical, {"normal": normal_frame, "player_seasons": season_frame, "params": params_out}


def validate_current_espn(items: list[dict], players: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Compare reconstructed 2026 player scores with ESPN's applied weekly totals."""
    year = LEAGUE_SEASON
    for tag, filename in [
        ("stats_player", f"stats_player_week_{year}.csv.gz"),
        ("snap_counts", f"snap_counts_{year}.csv.gz"),
        ("stats_team", f"stats_team_week_{year}.csv.gz"),
        ("pbp", f"play_by_play_{year}.csv.gz"),
    ]:
        _ensure_asset(tag, filename)
    offense, _ = _build_offense_games(range(year, year + 1), items, players)
    defense = _build_defense_games(range(year, year + 1), items)
    calculated = pd.concat([offense, defense], ignore_index=True)
    calculated["week"] = pd.to_numeric(calculated["week"], errors="coerce").astype(int)
    by_espn_id = calculated[calculated["position"].ne("DEF")].set_index(["week", "espn_id"])["points"].to_dict()
    by_def_team = calculated[calculated["position"].eq("DEF")].set_index(["week", "team"])["points"].to_dict()
    id_to_abbr = {}
    team_url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=32"
    try:
        request = urllib.request.Request(team_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            team_data = json.load(response)
        team_rows = team_data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        id_to_abbr = {str(row["team"]["id"]): str(row["team"]["abbreviation"]) for row in team_rows}
    except Exception:
        id_to_abbr = {}
    gsis_to_espn = {}
    for _, row in players.iterrows():
        gsis = str(row.get("gsis_id", "") or "")
        espn_id = _id_string(row.get("espn_id", ""))
        if gsis and espn_id and espn_id.lower() != "nan":
            gsis_to_espn[gsis] = espn_id
    espn_to_gsis = {value: key for key, value in gsis_to_espn.items()}
    rostered_rows = []
    for week in range(1, 19):
        url = (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/"
            f"leagues/{LEAGUE_ID}?view=mRoster&scoringPeriodId={week}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except Exception:
            continue
        for team in data.get("teams", []):
            for entry in (team.get("roster") or {}).get("entries", []):
                player = (entry.get("playerPoolEntry") or {}).get("player") or {}
                position_id = int(player.get("defaultPositionId") or 0)
                if position_id not in set(SLOT_IDS.values()):
                    continue
                weekly = next((
                    stat for stat in player.get("stats", [])
                    if int(stat.get("statSourceId", -1)) == 0
                    and int(stat.get("statSplitTypeId", -1)) == 1
                    and int(stat.get("scoringPeriodId", -1)) == week
                ), None)
                if not weekly or weekly.get("appliedTotal") is None:
                    continue
                if position_id == SLOT_IDS["DEF"]:
                    team_abbr = id_to_abbr.get(str(player.get("proTeamId", "")), "")
                    key = (week, team_abbr)
                    predicted = by_def_team.get(key)
                    player_key = f"DEF:{team_abbr}" if team_abbr else ""
                    position = "DEF"
                else:
                    espn_id = _id_string(player.get("id", ""))
                    gsis_id = espn_to_gsis.get(espn_id, "")
                    key = (week, espn_id)
                    predicted = by_espn_id.get(key)
                    player_key = gsis_id
                    position = next((name for name, slot in SLOT_IDS.items() if slot == position_id), "")
                if predicted is None:
                    continue
                rostered_rows.append({
                    "season": year, "week": week, "position": position,
                    "player_name": player.get("fullName", ""), "player_key": player_key,
                    "espn_score": _number(weekly.get("appliedTotal")),
                    "reconstructed_score": float(predicted),
                })
    validation = pd.DataFrame(rostered_rows)
    if validation.empty:
        summary = {"matched": 0, "note": "No current ESPN roster scores could be matched."}
    else:
        validation["absolute_error"] = (validation["espn_score"] - validation["reconstructed_score"]).abs()
        summary = {
            "matched": int(len(validation)),
            "exact_within_0_01": float((validation["absolute_error"] <= 0.01).mean()),
            "within_0_05": float((validation["absolute_error"] <= 0.05).mean()),
            "mean_absolute_error": float(validation["absolute_error"].mean()),
            "max_absolute_error": float(validation["absolute_error"].max()),
            "by_position": {
                position: {
                    "matched": int(len(group)),
                    "mean_absolute_error": float(group["absolute_error"].mean()),
                    "within_0_05": float((group["absolute_error"] <= 0.05).mean()),
                }
                for position, group in validation.groupby("position")
            },
        }
        validation.to_csv(HERE / "espn_score_validation.csv", index=False)
    return validation, summary


def _write_svg(model_frame: pd.DataFrame, params: dict, positive_games: pd.DataFrame) -> None:
    # A compact self-contained SVG avoids adding a plotting dependency.
    positions = [p for p in ALL_POSITIONS if p in params]
    width, panel_h, left, right, top = 960, 250, 58, 940, 42
    height = top + panel_h * len(positions) + 24
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="#fff"/>',
              '<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#555;stroke-width:1}.grid{stroke:#ddd;stroke-width:1}.hist{fill:#9bb9d6;opacity:.55}.curve{fill:none;stroke-width:2.5}</style>',
              '<text x="18" y="22" font-size="16" font-weight="bold">Positive weekly points, scaled by each player-season positive mean</text>',
              '<text x="18" y="38" font-size="11">Holdout seasons 2024–2025; curves fit on 2015–2023. Conditional on scoring more than zero.</text>']
    palette = {"truncated_normal": "#555555", "gamma": "#e07a22", "lognormal": "#2878b5", "two_lognormal": "#b43d64"}
    model_names = list(palette)
    for i, position in enumerate(positions):
        y0 = top + i * panel_h
        x0, x1 = left, right
        ytop, ybottom = y0 + 20, y0 + panel_h - 34
        span = max(0.25, float(positive_games.loc[(positive_games.position == position) & (positive_games.season >= HOLDOUT_FIRST_SEASON), "relative_score"].quantile(.99)))
        xvals = np.linspace(0.005, span, 240)
        hist = positive_games[(positive_games.position == position) & (positive_games.season >= HOLDOUT_FIRST_SEASON)]["relative_score"].to_numpy(dtype=float)
        hist = hist[np.isfinite(hist) & (hist > 0)]
        bins = np.linspace(0, span, 26)
        counts, _ = np.histogram(hist, bins=bins, density=True)
        max_y = max(float(counts.max()) if len(counts) else 0.1, 0.05)
        curves = {}
        for name in model_names:
            if name not in params[position]:
                continue
            p = params[position][name]
            curves[name] = np.exp(np.clip(_logpdf_model(xvals, name, p), -80, 20))
            if len(curves[name]): max_y = max(max_y, float(curves[name].max()))
        def sx(x): return x0 + (x / span) * (x1 - x0)
        def sy(y): return ybottom - (y / max_y) * (ybottom - ytop)
        pieces.append(f'<text x="{x0}" y="{ytop-5}" font-size="13" font-weight="bold">{position}</text>')
        for tick in [0, .5, 1, 1.5, 2, 2.5, 3]:
            if tick >= span: continue
            xp = sx(tick)
            pieces.append(f'<line class="grid" x1="{xp:.1f}" y1="{ytop}" x2="{xp:.1f}" y2="{ybottom}"/>')
            pieces.append(f'<text x="{xp:.1f}" y="{ybottom+16}" font-size="10" text-anchor="middle">{tick:g}</text>')
        pieces.append(f'<line class="axis" x1="{x0}" y1="{ybottom}" x2="{x1}" y2="{ybottom}"/>')
        pieces.append(f'<line class="axis" x1="{x0}" y1="{ytop}" x2="{x0}" y2="{ybottom}"/>')
        for j, count in enumerate(counts):
            bx0, bx1 = bins[j], bins[j+1]
            pieces.append(f'<rect class="hist" x="{sx(bx0):.1f}" y="{sy(count):.1f}" width="{max(1,sx(bx1)-sx(bx0)-1):.1f}" height="{ybottom-sy(count):.1f}"/>')
        for name, values in curves.items():
            path = " ".join(("M" if j == 0 else "L") + f"{sx(float(x)):.1f},{sy(float(y)):.1f}" for j, (x, y) in enumerate(zip(xvals, values)))
            pieces.append(f'<path class="curve" stroke="{palette[name]}" d="{path}"/>')
    legend_y = height - 8
    legend = [("Holdout histogram", "#779fc8", "rect"), ("Truncated normal", palette["truncated_normal"], "line"), ("Gamma", palette["gamma"], "line"), ("Lognormal", palette["lognormal"], "line"), ("Two-lognormal mixture", palette["two_lognormal"], "line")]
    x = 58
    for label, color, kind in legend:
        if kind == "rect": pieces.append(f'<rect x="{x}" y="{legend_y-10}" width="14" height="8" fill="{color}" opacity=".6"/>')
        else: pieces.append(f'<line x1="{x}" y1="{legend_y-6}" x2="{x+17}" y2="{legend_y-6}" stroke="{color}" stroke-width="2.5"/>')
        pieces.append(f'<text x="{x+22}" y="{legend_y-3}" font-size="10">{label}</text>')
        x += 150 if len(label) > 10 else 120
    pieces.append('</svg>')
    (HERE / "positive_shape_comparison.svg").write_text("\n".join(pieces), encoding="utf-8")


def write_report(games: pd.DataFrame, coverage: dict, model_frame: pd.DataFrame, empirical: pd.DataFrame, details: dict, validation_summary: dict) -> None:
    model_frame.to_csv(HERE / "model_comparison.csv", index=False)
    empirical.to_csv(HERE / "position_score_summary.csv", index=False)
    details["normal"].to_csv(HERE / "normal_fit_diagnostics.csv", index=False)
    details["player_seasons"].to_csv(HERE / "player_season_shape_data.csv", index=False)
    (HERE / "fit_parameters.json").write_text(json.dumps(details["params"], indent=2) + "\n", encoding="utf-8")
    games.to_csv(HERE / "scored_player_games.csv", index=False)
    pos_positive = games[games["points"] > 0].merge(
        details["player_seasons"][["position", "player_key", "season", "positive_mean"]],
        on=["position", "player_key", "season"], how="inner",
    )
    pos_positive["relative_score"] = pos_positive["points"] / pos_positive["positive_mean"]
    _write_svg(model_frame, details["params"], pos_positive)
    summary = empirical[empirical["split"].eq("holdout")].set_index("position")
    diagnostics = details["normal"]
    lines = [
        "# Player weekly score distribution study",
        "",
        f"Data: NFL regular-season player-games from {DATA_FIRST_SEASON}–{DATA_LAST_SEASON}, scored using the league's current ESPN settings from {LEAGUE_SEASON}.",
        "The training period is 2015–2023; 2024–2025 are held out for model comparison.",
        "",
        "## Data and scoring",
        "",
        "Weekly player stats, team stats, play-by-play, and snap counts are from the public [nflverse data releases](https://github.com/nflverse/nflverse-data). ESPN's public league settings endpoint supplied the current scoring items. Scores are recalculated for this league instead of taking nflverse's default PPR totals.",
        "",
        "Only regular-season games with at least one offensive snap are included for QB/RB/WR/TE; kickers need a special-teams snap. This keeps active zero-point games while excluding bye weeks and DNPs. D/ST rows come from each team's weekly defensive stats.",
        "",
        f"Data coverage: {coverage.get('active_offense_and_k_games', 0):,} offensive-player/kicker active games, {coverage.get('defense_games', 0):,} D/ST games, {coverage.get('total_games', 0):,} total position-games.",
        "",
        f"Current ESPN cross-check: {validation_summary.get('matched', 0):,} rostered player-games matched; mean absolute error {validation_summary.get('mean_absolute_error', float('nan')):.3f} points; within 0.05 points {validation_summary.get('within_0_05', float('nan')):.1%}.",
        "",
        "The cross-check uses the league's live weekly applied scores. Scoring rules are configured through ESPN's [scoring settings](https://support.espn.com/hc/en-us/articles/360003914032-Scoring-Formats).",
        "",
        "| Position | Matched games | Within 0.05 points | Mean absolute error |",
        "|---|---:|---:|---:|",
    ]
    for position in ALL_POSITIONS:
        row = validation_summary.get("by_position", {}).get(position)
        if row:
            lines.append(
                f"| {position} | {row['matched']:,} | {row['within_0_05']:.1%} | {row['mean_absolute_error']:.3f} |"
            )
    lines += [
        "",
        "The current league is custom PPR: 1 point per reception, 0.1 per rushing/receiving yard, 0.04 per passing yard, 4-point passing TDs, 6-point rushing/receiving TDs, −2 interceptions, −2 lost fumbles, and −0.5 per QB sack, plus the league's yardage, long-TD, kicking, and defense bonuses. The settings are saved in `scoring_settings.json`.",
        "",
        "## What the data says about the normal fit",
        "",
        "The normal diagnostic fits each player-season's own mean and SD, then compares its implied low-score probabilities with that season's actual games. This is a shape diagnostic, not a future-week forecast.",
        "",
        "| Position | Holdout games | Zero | Negative | Under 1 point | Normal predicts below 0 | Normal predicts under 1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    diag_hold = diagnostics[diagnostics["split"].eq("holdout")].set_index("position")
    for position in ALL_POSITIONS:
        if position not in summary.index or position not in diag_hold.index:
            continue
        s, d = summary.loc[position], diag_hold.loc[position]
        lines.append(
            f"| {position} | {int(s['games']):,} | {s['zero_rate']:.1%} | {s['negative_rate']:.1%} | {s['under_1_rate']:.1%} | {d['normal_predicted_negative_rate']:.1%} | {d['normal_predicted_under_1_rate']:.1%} |"
        )
    lines += [
        "",
        "The table shows whether a normal curve assigns too much probability below zero or misses the observed low-score cluster. Exact zero and negative scores are kept as separate outcomes in the proposed display model; they are not smoothed into the positive-score curve.",
        "",
        "## Candidate positive-score distributions",
        "",
        "For each player-season with at least eight active games and three positive scores, each positive weekly score is divided by that player's positive-game mean. This removes most between-player scoring-level differences and lets the data estimate the within-player shape. Candidate positive components are a normal truncated at zero, Gamma, lognormal, and a two-lognormal mixture. Training uses 2015–2023; reported test log scores use 2024–2025.",
        "",
        "| Position | Model with best held-out log score | Held-out mean log score | Observed fraction below 25% of positive mean | Model probability below 25% |",
        "|---|---|---:|---:|---:|",
    ]
    if not model_frame.empty:
        for position in ALL_POSITIONS:
            rows = model_frame[model_frame["position"].eq(position)]
            if rows.empty:
                continue
            best = rows.sort_values("test_mean_log_score", ascending=False).iloc[0]
            lines.append(
                f"| {position} | {best['model']} | {best['test_mean_log_score']:.3f} | {best['test_observed_fraction_under_0_25']:.1%} | {best['test_p_under_0_25_of_positive_mean']:.1%} |"
            )
    lines += [
        "",
        "![Position-specific positive-score curves](positive_shape_comparison.svg)",
        "",
        "## Recommended player-level curve",
        "",
        "Use a three-part model: a position- and player-specific negative-score component, a point mass at exactly zero, and a position-specific positive-score distribution. The positive part should use the held-out winner by position: Gamma for WR; Gamma or the two-lognormal mixture for RB (their scores are effectively tied); and the two-lognormal mixture for QB, TE, K, and DEF. Scale the positive shape to the player's typical positive-game score. Estimate each player's zero and negative rates from their own history, shrinking toward the same-position training baselines when the sample is short. Keep the actual negative-score values as a separate left-tail distribution rather than collapsing them into one probability. The training baseline masses and empirical negative-score distributions are in `fit_parameters.json`.",
        "",
        "This is more faithful to the sketch than a normal curve, but pooled raw scores should not be used directly for an individual player: they mix backups and stars. The scale adjustment and explicit zero/negative masses address that distinction. A full opportunity-count × event-outcome simulation would be a later forecasting model, not required for this historical hover visualization.",
        "",
        "## Limits",
        "",
        "This analysis applies the current 2026 league scoring settings retroactively to 2015–2025 stats; it does not reproduce each historical season's original rules. nflverse weekly player stats omit some ESPN-specific stat corrections, and rare D/ST event categories (especially one-point safeties and unusual return plays) may not match ESPN perfectly. The curve selection is based on held-out distributional log score, but it is not yet integrated into the dashboard.",
        "",
        "Raw source files are cached under this folder's ignored `.cache/` directory. nflverse data releases are broadly licensed CC-BY 4.0; see the source repository for attribution details.",
    ]
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-scoring", action="store_true", help="Re-fetch live ESPN scoring settings.")
    parser.add_argument("--years", nargs=2, type=int, default=[DATA_FIRST_SEASON, DATA_LAST_SEASON], metavar=("FIRST", "LAST"))
    args = parser.parse_args()
    years = range(args.years[0], args.years[1] + 1)
    download_inputs(years)
    profile = load_scoring_settings(args.refresh_scoring)
    items = profile["scoring_items"]
    players_path = _ensure_asset("players", "players.csv.gz")
    players = _read_csv(players_path, {"pfr_id", "gsis_id", "espn_id", "display_name"})
    offense, coverage = _build_offense_games(years, items, players)
    defense = _build_defense_games(years, items)
    games = pd.concat([offense, defense], ignore_index=True)
    games["season"] = pd.to_numeric(games["season"], errors="coerce").astype(int)
    games["week"] = pd.to_numeric(games["week"], errors="coerce").astype(int)
    games["points"] = pd.to_numeric(games["points"], errors="coerce").fillna(0.0)
    games = games.drop_duplicates(["season", "game_id", "player_key"]).sort_values(["season", "week", "position", "player_key"])
    coverage["defense_games"] = int(len(defense))
    coverage["total_games"] = int(len(games))
    coverage["position_games"] = {str(k): int(v) for k, v in games["position"].value_counts().to_dict().items()}
    model_frame, empirical, details = _fit_and_compare(games)
    validation, validation_summary = validate_current_espn(items, players)
    write_report(games, coverage, model_frame, empirical, details, validation_summary)
    print(json.dumps({
        "coverage": coverage,
        "current_espn_validation": validation_summary,
        "summary": empirical[empirical["split"].eq("holdout")].to_dict(orient="records"),
        "best_models": model_frame.sort_values("test_mean_log_score", ascending=False).groupby("position").head(1).to_dict(orient="records"),
        "files": ["report.md", "position_score_summary.csv", "normal_fit_diagnostics.csv", "model_comparison.csv", "positive_shape_comparison.svg"],
    }, indent=2))


if __name__ == "__main__":
    main()
