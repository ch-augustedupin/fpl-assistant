"""Expected-points model: form + condition + fixtures -> xPts per player per gameweek.

Every rate is a recency-weighted per-90 blend of (this season, last season, position average), so a
single haul doesn't dominate and new signings still get a sensible estimate.
"""
import math
import unicodedata
from collections import defaultdict

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
DECAY = 0.8          # weight of a gameweek relative to the one after it (form = recent GWs matter most)
HOME_ADV = 1.10      # goal multiplier at home (1/HOME_ADV away)
TEAM_PRIOR_GAMES = 6  # shrink team strength toward its pre-season rating by this many matches
PAST_GAMES_MAX = 8   # last season's per-90 rates count as at most this many matches
POS_PRIOR_GAMES = 1.5
DC_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


def poisson_at_least(mu, k):
    if mu <= 0:
        return 0.0
    p, term = 0.0, math.exp(-mu)
    for i in range(k):
        p += term
        term *= mu / (i + 1)
    return max(0.0, 1.0 - p)


def expected_half_goals(lam):
    """E[floor(G/2)] for G ~ Poisson(lam): the -1 per 2 goals conceded penalty."""
    e, term = 0.0, math.exp(-lam)
    for k in range(0, 15):
        if k:
            term *= lam / k
        e += (k // 2) * term
    return e


class Model:
    def __init__(self, boot, fixtures, histories, cutoff, overrides=None, europe_teams=(), use_flags=True):
        """cutoff = last finished GW whose data may be used (lower it to backtest)."""
        self.boot, self.fixtures, self.hist, self.cutoff = boot, fixtures, histories, cutoff
        self.overrides = overrides or {}
        self.use_flags = use_flags
        self.scoring = boot["game_config"]["scoring"]
        self.teams = {t["id"]: t for t in boot["teams"]}
        self.short = {t["id"]: t["short_name"] for t in boot["teams"]}
        self.europe = {tid for tid, t in self.teams.items() if t["short_name"] in set(europe_teams)}
        self.fix_by_id = {f["id"]: f for f in fixtures}
        self.players = {p["id"]: p for p in boot["elements"]}
        self._team_strength()
        self._player_rates()

    # ---------- teams ----------
    def _team_strength(self):
        """Attack/defence multipliers from xG (75%) and goals (25%) in finished fixtures, shrunk to a prior."""
        team_xg = defaultdict(float)  # (fixture, team) -> xG
        for pid, h in self.hist.items():
            for row in h["history"]:
                if row["round"] > self.cutoff:
                    continue
                f = self.fix_by_id.get(row["fixture"])
                if not f:
                    continue
                team = f["team_h"] if row["was_home"] else f["team_a"]
                team_xg[(f["id"], team)] += float(row["expected_goals"])
        stats = defaultdict(lambda: {"n": 0, "for": 0.0, "against": 0.0})
        for f in self.fixtures:
            if not f["finished"] or f["event"] is None or f["event"] > self.cutoff:
                continue
            h, a = f["team_h"], f["team_a"]
            gh = 0.75 * team_xg[(f["id"], h)] + 0.25 * f["team_h_score"]
            ga = 0.75 * team_xg[(f["id"], a)] + 0.25 * f["team_a_score"]
            # remove home advantage so ratings are venue-neutral
            stats[h]["n"] += 1; stats[h]["for"] += gh / HOME_ADV; stats[h]["against"] += ga * HOME_ADV
            stats[a]["n"] += 1; stats[a]["for"] += ga * HOME_ADV; stats[a]["against"] += gh / HOME_ADV
        total_n = sum(s["n"] for s in stats.values())
        self.mu = (sum(s["for"] for s in stats.values()) / total_n) if total_n else 1.35
        self.att, self.dfn = {}, {}
        for tid, t in self.teams.items():
            prior_s = (t["strength_overall_home"] + t["strength_overall_away"]) / 2
            prior_att = 1 + 0.18 * (prior_s - 3)
            prior_def = 1 - 0.15 * (prior_s - 3)  # >1 = leaky defence
            s = stats[tid]
            k = TEAM_PRIOR_GAMES
            att = (s["for"] / s["n"] / self.mu) if s["n"] else prior_att
            dfn = (s["against"] / s["n"] / self.mu) if s["n"] else prior_def
            self.att[tid] = (s["n"] * att + k * prior_att) / (s["n"] + k)
            self.dfn[tid] = (s["n"] * dfn + k * prior_def) / (s["n"] + k)

    def lam(self, team, opp, home):
        """Expected goals scored by `team` against `opp`."""
        return self.mu * self.att[team] * self.dfn[opp] * (HOME_ADV if home else 1 / HOME_ADV)

    # ---------- players ----------
    def _rows(self, pid):
        return [r for r in self.hist.get(pid, {}).get("history", []) if r["round"] <= self.cutoff]

    def _player_rates(self):
        stat_keys = ("xg", "xa", "saves", "bonus", "yc", "pts")
        raw = {}
        pos_sum = defaultdict(lambda: defaultdict(float))
        for pid, p in self.players.items():
            pos = POS[p["element_type"]]
            rows = self._rows(pid)
            played = [r for r in rows if r["minutes"] > 0]
            w_min = defaultdict(float)
            cur = defaultdict(float)
            dc_hit_w = dc_w = 0.0
            for r in played:
                w = DECAY ** (self.cutoff - r["round"])
                w_min["m"] += w * r["minutes"]
                cur["xg"] += w * float(r["expected_goals"])
                cur["xa"] += w * float(r["expected_assists"])
                cur["saves"] += w * r["saves"]
                cur["bonus"] += w * r["bonus"]
                cur["yc"] += w * r["yellow_cards"]
                cur["pts"] += w * r["total_points"]
                if r["minutes"] >= 60 and pos in DC_THRESHOLD:
                    dc_w += w
                    dc_hit_w += w * (r["defensive_contribution"] >= DC_THRESHOLD[pos])
            past = next((s for s in reversed(self.hist.get(pid, {}).get("history_past", []))
                         if s["minutes"] >= 450), None)
            raw[pid] = (pos, w_min["m"], cur, dc_hit_w, dc_w, past, rows)
            for k in stat_keys:
                pos_sum[pos][k] += cur[k]
            pos_sum[pos]["m"] += w_min["m"]
        pos_mean = {pos: {k: (v[k] / v["m"] * 90 if v["m"] else 0) for k in stat_keys} for pos, v in pos_sum.items()}

        self.rates = {}
        for pid, (pos, wm, cur, dc_hit_w, dc_w, past, rows) in raw.items():
            g_cur = wm / 90
            g_past = min(past["minutes"] / 90, PAST_GAMES_MAX) * 0.6 if past else 0
            past_map = {}
            if past:
                pm = past["minutes"] / 90
                past_map = {"xg": float(past["expected_goals"]) / pm, "xa": float(past["expected_assists"]) / pm,
                            "saves": past["saves"] / pm, "bonus": past["bonus"] / pm,
                            "yc": past["yellow_cards"] / pm, "pts": past["total_points"] / pm}
            r = {}
            for k in pos_mean[pos]:
                num = cur[k] + g_past * past_map.get(k, 0) + POS_PRIOR_GAMES * pos_mean[pos][k] * 0.7
                r[k] = num / (g_cur + g_past + POS_PRIOR_GAMES)
            # defensive contribution: P(hitting the threshold in a 60+ min appearance)
            if pos in DC_THRESHOLD:
                prior = poisson_at_least(past["defensive_contribution"] / (past["minutes"] / 90), DC_THRESHOLD[pos]) \
                    if past else 0.15
                r["dc"] = (dc_hit_w + 3 * prior) / (dc_w + 3)
            else:
                r["dc"] = 0.0
            r.update(self._minutes(pid, rows, past))
            r.update(self._form(rows))
            self.rates[pid] = r

    def _minutes(self, pid, rows, past):
        """Probability of starting / coming on, and minutes when starting, from the last 6 GWs."""
        recent = sorted(rows, key=lambda r: r["round"])[-6:]
        if len(recent) >= 2:
            ws = [DECAY ** (self.cutoff - r["round"]) for r in recent]
            tw = sum(ws)
            start = sum(w * r["starts"] for w, r in zip(ws, recent)) / tw
            sub = sum(w * (r["minutes"] > 0 and not r["starts"]) for w, r in zip(ws, recent)) / tw
            started = [r for r in recent if r["starts"]]
            mins_start = sum(r["minutes"] for r in started) / len(started) if started else 75
            p60_given_start = sum(r["minutes"] >= 60 for r in started) / len(started) if started else 0.7
        else:  # new signing or no data yet: lean on last season
            start = min(0.85, past["starts"] / 38) if past else 0.25
            sub = 0.2
            mins_start, p60_given_start = 80, 0.85
        last3 = sorted(rows, key=lambda r: r["round"])[-3:]
        return {"p_start": start, "p_sub": sub, "mins_start": mins_start, "p60_start": p60_given_start,
                "mins_last3": sum(r["minutes"] for r in last3)}

    def _form(self, rows):
        played = sorted(rows, key=lambda r: r["round"])
        last4 = played[-4:]
        m4 = sum(r["minutes"] for r in last4)
        xgi90_4 = sum(float(r["expected_goal_involvements"]) for r in last4) / m4 * 90 if m4 >= 90 else 0
        pts_last3 = [r["total_points"] for r in played[-3:]]
        avg3 = sum(pts_last3) / len(pts_last3) if pts_last3 else 0
        avg_all = sum(r["total_points"] for r in played) / len(played) if played else 0
        if not played or len(played) < 3:
            trend = "·"
        elif avg3 >= max(avg_all * 1.2, avg_all + 1):
            trend = "↑"
        elif avg3 <= avg_all * 0.75:
            trend = "↓"
        else:
            trend = "→"
        return {"xgi90_4": xgi90_4, "pts_last3": pts_last3, "trend": trend}

    # ---------- availability ----------
    def availability(self, pid, offset):
        """Chance of being available `offset` GWs after the next one (0 = next GW)."""
        if not self.use_flags:
            return 1.0
        p = self.players[pid]
        if pid in self.overrides:
            c = self.overrides[pid]
            return c / 100 if offset == 0 else min(1.0, c / 100 + 0.35 * offset)
        if p["status"] == "u":
            return 0.0
        c = p["chance_of_playing_next_round"]
        if c is None:
            c = 100 if p["status"] in ("a", "d") else 0
        news = p["news"].lower()
        long_term = "unknown return" in news or "season" in news
        if offset == 0:
            return c / 100
        if c >= 75:
            return 1.0
        step = 0.15 if long_term else 0.35
        return min(1.0, c / 100 + step * offset)

    # ---------- fixtures ----------
    def team_fixtures(self, gw):
        out = defaultdict(list)
        for f in self.fixtures:
            if f["event"] == gw:
                out[f["team_h"]].append((f["team_a"], True))
                out[f["team_a"]].append((f["team_h"], False))
        return out

    # ---------- expected points ----------
    def fixture_points(self, pid, opp, home, avail):
        p = self.players[pid]
        team, pos, r, sc = p["team"], POS[p["element_type"]], self.rates[pid], self.scoring
        p_start = r["p_start"]
        if team in self.europe and p_start < 0.9:
            p_start *= 0.95  # midweek European games add rotation risk for non-nailed players
        p_start *= avail
        p_sub = r["p_sub"] * avail
        mins = p_start * r["mins_start"] + p_sub * 20
        p60 = p_start * r["p60_start"]
        own_lam = self.lam(team, opp, home)
        opp_lam = self.lam(opp, team, not home)
        att_factor = own_lam / (self.mu * self.att[team])  # opponent quality + venue, ~1 on average
        m90 = mins / 90
        pts = (p_start + p_sub) * 1 + p60 * 1
        pts += r["xg"] * m90 * att_factor * sc["goals_scored"][pos]
        pts += r["xa"] * m90 * att_factor * sc["assists"]
        pts += p60 * math.exp(-opp_lam) * sc["clean_sheets"][pos]
        if pos in ("GKP", "DEF"):
            pts += m90 * expected_half_goals(opp_lam) * sc["goals_conceded"][pos]
        if pos == "GKP":
            pts += 0.85 * r["saves"] * m90 * (opp_lam / self.mu) / 3 * sc["saves"]
        if pos in DC_THRESHOLD:
            pts += p60 * r["dc"] * sc["defensive_contribution"][pos]
        pts += r["bonus"] * m90 * (0.5 + 0.5 * att_factor)
        pts += r["yc"] * m90 * sc["yellow_cards"]
        return pts

    def predict(self, gws):
        """{pid: [xPts per GW in gws]} plus per-player info for the report."""
        fx = {gw: self.team_fixtures(gw) for gw in gws}
        out = {}
        for pid, p in self.players.items():
            xp = []
            for k, gw in enumerate(gws):
                avail = self.availability(pid, k)
                xp.append(sum(self.fixture_points(pid, opp, home, avail) for opp, home in fx[gw].get(p["team"], [])))
            out[pid] = xp
        return out

    # ---------- explanations ----------
    def fixture_text(self, team, gws, n=3):
        parts = []
        for gw in gws[:n]:
            fs = self.team_fixtures(gw).get(team, [])
            if not fs:
                parts.append("BLANK")
            for opp, home in fs:
                parts.append(f"{self.short[opp]}({'H' if home else 'A'})")
        return " ".join(parts)

    def ease(self, team, gws, pos, n=3):
        """Fixture ease over the next n GWs (>1 = easier than average): attackers face leaky defences,
        goalkeepers/defenders face weak attacks."""
        vals = []
        for gw in gws[:n]:
            for opp, home in self.team_fixtures(gw).get(team, []):
                venue = HOME_ADV if home else 1 / HOME_ADV
                if pos in ("GKP", "DEF"):
                    vals.append(venue / self.att[opp])
                else:
                    vals.append(self.dfn[opp] * venue)
        return sum(vals) / len(vals) if vals else 0

    def flags(self, pid, next_gw):
        p, r = self.players[pid], self.rates[pid]
        f = []
        if pid in self.overrides:
            f.append(f"override {self.overrides[pid]}%")
        elif p["status"] != "a" or p["news"]:
            c = p["chance_of_playing_next_round"]
            date = (p["news_added"] or "")[:10]
            f.append(f"⚠ {c if c is not None else '?'}% — {p['news'] or p['status']} ({date})")
        if 0.35 <= r["p_start"] < 0.8:
            f.append(f"rotation risk (starts {r['p_start']:.0%})")
        elif r["p_start"] >= 0.8 and r["mins_start"] < 68:
            f.append(f"often subbed (~{r['mins_start']:.0f}′)")
        if p["team"] in self.europe and r["p_start"] < 0.9:
            f.append("Europe midweek")
        yc = p["yellow_cards"]
        if (next_gw <= 19 and yc == 4) or (next_gw <= 32 and yc == 9) or yc == 14:
            f.append(f"1 yellow from a ban ({yc} YC)")
        if p["cost_change_event"] > 0:
            f.append(f"price ↑ £{p['cost_change_event'] / 10:.1f}m this GW")
        elif p["cost_change_event"] < 0:
            f.append(f"price ↓ £{-p['cost_change_event'] / 10:.1f}m this GW")
        net = p["transfers_in_event"] - p["transfers_out_event"]
        if abs(net) > 150_000:
            f.append(f"net transfers {net / 1000:+.0f}k (price {'rise' if net > 0 else 'fall'} likely)")
        return f

    def reason(self, pid, gws):
        p, r = self.players[pid], self.rates[pid]
        pos = POS[p["element_type"]]
        bits = [f"form {r['trend']} (last3 {'/'.join(map(str, r['pts_last3'])) or '-'})"]
        if pos in ("MID", "FWD") or r["xgi90_4"] > 0.25:
            bits.append(f"xGI {r['xgi90_4']:.2f}/90 last4")
        if pos in ("DEF", "MID") and r["dc"] > 0.3:
            bits.append(f"DC {r['dc']:.0%}")
        if p["status"] == "a" and not p["news"]:
            bits.append("fit")
        e = self.ease(p["team"], gws, pos)
        bits.append(("easy" if e > 1.08 else "hard" if e < 0.92 else "avg") + " fixtures: " +
                    self.fixture_text(p["team"], gws))
        return ", ".join(bits)
