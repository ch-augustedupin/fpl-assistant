"""Head-to-head helpers: this week's opponent and league ownership."""
import math

from . import api
from .optimize import lineup

DIFF_SD = 22  # rough SD of the weekly points difference between two FPL teams


def win_probability(diff):
    return 0.5 * (1 + math.erf(diff / (DIFF_SD * math.sqrt(2))))


def opponent(league, gw, team_id):
    """(opponent entry id, name, manager) for team_id in gameweek gw, or None (bye / not found)."""
    for m in api.h2h_matches(league, gw):
        if m["entry_1_entry"] == team_id:
            return m["entry_2_entry"], m["entry_2_name"], m["entry_2_player_name"]
        if m["entry_2_entry"] == team_id:
            return m["entry_1_entry"], m["entry_1_name"], m["entry_1_player_name"]
    return None


def league_squads(league, last_gw):
    """{entry_id: [15 pids]} for every team in the league, as of the last deadline."""
    out = {}
    for r in api.h2h_standings(league)["standings"]["results"]:
        try:
            out[r["entry"]] = [p["element"] for p in api.entry_picks(r["entry"], last_gw)["picks"]]
        except RuntimeError:
            pass  # team created after last_gw
    return out


def preview(players, xp_next, my_xi, my_cap, opp_squad):
    """Compare my planned XI with the opponent's best XI from their current squad."""
    opp = lineup(players, xp_next, opp_squad)
    mine = set(my_xi)
    theirs = set(opp["xi"])
    my_xp = sum(xp_next[i] for i in my_xi) + xp_next[my_cap]
    return {
        "opp": opp,
        "shared": sorted(mine & theirs, key=lambda i: -xp_next[i]),
        "my_diff": sorted(mine - theirs, key=lambda i: -xp_next[i]),
        "their_diff": sorted(theirs - mine, key=lambda i: -xp_next[i]),
        "my_xp": my_xp,
        "p_win": win_probability(my_xp - opp["xp"]),
    }
