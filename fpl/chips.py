"""Chip advisor: is this gameweek a good moment for a chip, compared with the rest of its window?

Each chip is valid in a window (GW1–19 or GW20–38 in 2026/27). For Triple Captain and Bench Boost the
value this week is compared with every later week of the window (same squad, so the future is approximate);
Free Hit and Wildcard are judged by how much a rebuilt team would add.
"""
from . import h2h
from .optimize import lineup, optimise

NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
TC_MIN, BB_MIN, FH_MIN, WC_MIN = 6.5, 10.0, 12.0, 15.0


def windows(boot, history, gw):
    """{chip name: last GW of its current window} for chips still available in gameweek gw."""
    used = [(c["name"], c["event"]) for c in history["chips"]]
    out = {}
    for c in boot["chips"]:
        if c["start_event"] <= gw <= c["stop_event"] and not any(
                n == c["name"] and c["start_event"] <= e <= c["stop_event"] for n, e in used):
            out[NAMES.get(c["name"], c["name"])] = c["stop_event"]
    return out


def advise(model, players, squad, sell, bank, ft, xp_window, gws_window, avail, base_xp, p_win_diff=None,
           wildcard_gain=None):
    """
    xp_window: {pid: [xPts for each GW in gws_window]}, gws_window starts at the next GW.
    avail: {chip: last GW of window}. base_xp: predicted points of the recommended line-up this week.
    p_win_diff: predicted H2H points difference (mine - theirs) without a chip, or None.
    Returns a list of {"chip", "verdict" ("play"|"consider"|"save"), "reason", "short"}.
    """
    advice = []
    g0 = gws_window[0]

    def win_text(extra):
        if p_win_diff is None:
            return ""
        a, b = h2h.win_probability(p_win_diff), h2h.win_probability(p_win_diff + extra)
        return f"; H2H win chance {a:.0%} → {b:.0%}"

    def per_gw(stop):
        return [k for k, g in enumerate(gws_window) if g <= stop]

    def verdict(now, later, minimum, last):
        best_later = max(later) if later else 0
        if last:
            return "play" if now > 0 else "save"
        if now >= minimum and now >= best_later - 0.3:
            return "play"
        if now >= minimum and now >= best_later - 1.0:
            return "consider"
        return "save"

    if "Triple Captain" in avail:
        ks = per_gw(avail["Triple Captain"])
        caps = [max(squad, key=lambda i: xp_window[i][k]) for k in ks]
        vals = [xp_window[c][k] for c, k in zip(caps, ks)]
        now, later = vals[0], vals[1:]
        last = avail["Triple Captain"] == g0
        v = verdict(now, later, TC_MIN, last)
        name = players[caps[0]]["web_name"]
        if later:
            kb = max(range(len(later)), key=lambda j: later[j])
            cmp_ = f"best later: GW{gws_window[ks[kb + 1]]} {players[caps[kb + 1]]['web_name']} {later[kb]:.1f}"
        else:
            cmp_ = "last gameweek of the window"
        advice.append({"chip": "Triple Captain", "verdict": v,
                       "reason": f"{name} {now:.1f} xPts this week ({cmp_}; chip expires after "
                                 f"GW{avail['Triple Captain']}){win_text(now)}",
                       "short": f"Triple Captain on {name} ({now:.1f} xPts)"})

    if "Bench Boost" in avail:
        ks = per_gw(avail["Bench Boost"])
        vals = []
        for k in ks:
            xg = {i: xp_window[i][k] for i in squad}
            lu = lineup(players, xg, squad)
            vals.append(sum(xg[i] for i in lu["bench"]))
        now, later = vals[0], vals[1:]
        v = verdict(now, later, BB_MIN, avail["Bench Boost"] == g0)
        kb = max(range(len(later)), key=lambda j: later[j]) if later else None
        cmp_ = f"best later: GW{gws_window[ks[kb + 1]]} {later[kb]:.1f}" if later else "last gameweek of the window"
        advice.append({"chip": "Bench Boost", "verdict": v,
                       "reason": f"bench worth {now:.1f} xPts this week ({cmp_}){win_text(now)}",
                       "short": f"Bench Boost (bench {now:.1f} xPts)"})

    if "Free Hit" in avail:
        xp1 = {i: [v[0]] for i, v in xp_window.items()}
        fh = optimise(players, xp1, squad, sell, bank, ft, None)
        gain = (fh["xp_next"] - base_xp) if fh else 0
        blanks = sum(1 for i in squad if not model.team_fixtures(g0).get(players[i]["team"]))
        last = avail["Free Hit"] == g0
        v = "play" if gain >= FH_MIN or (last and gain > 4) else "consider" if gain >= FH_MIN * 0.75 else "save"
        odd = [g for g in gws_window[1:] if g <= avail["Free Hit"] and _odd_week(model, g)]
        hint = f"; blank/double later in GW{', GW'.join(map(str, odd[:3]))} — better use there" if odd and v == "save" else ""
        advice.append({"chip": "Free Hit", "verdict": v,
                       "reason": f"a one-week team would add {gain:+.1f} xPts ({blanks} of your players blank)" + hint,
                       "short": f"Free Hit ({gain:+.0f} xPts)"})

    if "Wildcard" in avail and wildcard_gain is not None:
        last = avail["Wildcard"] == g0
        v = "consider" if wildcard_gain >= WC_MIN or last else "save"
        advice.append({"chip": "Wildcard", "verdict": v,
                       "reason": f"a rebuilt squad would add {wildcard_gain:+.1f} weighted xPts over the next weeks"
                                 + (" — last week of this Wildcard" if last else ""),
                       "short": f"Wildcard ({wildcard_gain:+.0f} xPts)"})
    return advice


def _odd_week(model, g):
    tf = model.team_fixtures(g)
    return any(len(f) > 1 for f in tf.values()) or len(tf) < 20


def opponent_threat(boot, opp_history, gw, opp_xi, opp_captain, xp1, players):
    """Chips the opponent still has, with a note when one looks tempting for them this week."""
    avail = windows(boot, opp_history, gw)
    notes = []
    if "Triple Captain" in avail and xp1[opp_captain] >= TC_MIN + 0.5:
        notes.append(f"Triple Captain on {players[opp_captain]['web_name']} ({xp1[opp_captain]:.1f} xPts) "
                     f"would add ~{xp1[opp_captain]:.0f} pts to their score")
    if "Triple Captain" in avail and avail["Triple Captain"] - gw <= 1:
        notes.append("their Triple Captain expires soon — likely to be played")
    if "Bench Boost" in avail and avail["Bench Boost"] - gw <= 1:
        notes.append("their Bench Boost expires soon — likely to be played")
    return list(avail), notes
