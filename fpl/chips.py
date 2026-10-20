"""Chip advisor: is this gameweek a good moment for a chip, compared with the rest of its window?

Each chip is valid in a window (GW1–19 or GW20–38 in 2026/27). For Triple Captain and Bench Boost the
value this week is compared with every later week of the window (same squad, so the future is approximate);
Free Hit and Wildcard are judged by how much a rebuilt team would add.
"""
from . import h2h
from .optimize import lineup, optimise

NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
TC_MIN, BB_MIN, FH_MIN, WC_MIN = 6.5, 10.0, 12.0, 15.0
LEAGUE_MIN = 0.25  # league points a chip must add this week (≈ +8% win chance in a normal match)


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
           wildcard_gain=None, h2h_window=None):
    """
    xp_window: {pid: [xPts for each GW in gws_window]}, gws_window starts at the next GW.
    avail: {chip: last GW of window}. base_xp: predicted points of the recommended line-up this week.
    p_win_diff: predicted H2H points difference (mine - theirs) this week without a chip, or None.
    h2h_window: per GW of gws_window, None or {"name", "opp_xp", "weight"}: the H2H opponent that week, their
      predicted score and how much the match counts (1.5 against a direct rival in the table).
    In a H2H league a chip is worth the league points it adds: 3 × weight × (win chance with − without).
    Returns a list of {"chip", "verdict" ("play"|"consider"|"save"), "reason", "short"}.
    """
    advice = []
    g0 = gws_window[0]
    my_xp = {}

    def mine(k):  # my best line-up with the current squad in GW k (captain included)
        if k not in my_xp:
            my_xp[k] = lineup(players, {i: xp_window[i][k] for i in squad}, squad)["xp"]
        return my_xp[k]

    def match(k):
        return h2h_window[k] if h2h_window and k < len(h2h_window) else None

    def league_gain(k, extra):
        m = match(k)
        if not m:
            return None
        diff = (p_win_diff if k == 0 and p_win_diff is not None else mine(k) - m["opp_xp"])
        return 3 * m["weight"] * (h2h.win_probability(diff + extra) - h2h.win_probability(diff))

    def win_text(k, extra):
        m = match(k)
        if not m:
            return ""
        diff = (p_win_diff if k == 0 and p_win_diff is not None else mine(k) - m["opp_xp"])
        a, b = h2h.win_probability(diff), h2h.win_probability(diff + extra)
        rival = ", direct rival" if m["weight"] > 1 else ""
        return f"vs {m['name']}{rival}: win {a:.0%} → {b:.0%}"

    def per_gw(stop):
        return [k for k, g in enumerate(gws_window) if g <= stop]

    def judge(ks, extras, minimum_xp, last, label):
        """Pick play/consider/save from league-point gains when the H2H schedule is known, else from xPts."""
        now_x = extras[0]
        # later weeks are less certain (predictions, injuries, squad changes): discount them a little, so the
        # best of many noisy future weeks doesn't always beat a good week now
        gains = [None if g is None else g * max(0.75, 1 - 0.025 * k)
                 for g, k in ((league_gain(k, e), k) for k, e in zip(ks, extras))]
        if gains[0] is not None:
            later = [(g, k, e) for g, k, e in zip(gains[1:], ks[1:], extras[1:]) if g is not None]
            best = max(later, key=lambda t: t[0]) if later else None
            if last:
                v = "play"
            elif gains[0] >= LEAGUE_MIN and (not best or gains[0] >= best[0] - 0.03):
                v = "play"
            elif gains[0] >= LEAGUE_MIN * 0.6 and (not best or gains[0] >= best[0] * 0.8):
                v = "consider"
            else:
                v = "save"
            text = f"{label(0)} {now_x:.1f} xPts this week, {win_text(0, now_x)} = +{gains[0]:.2f} league pts"
            if best:
                text += (f"; best later: GW{gws_window[best[1]]} {win_text(best[1], best[2])}, "
                         f"{label(best[1])} {best[2]:.1f} xPts = +{best[0]:.2f} league pts (discounted)")
            return v, text
        later = extras[1:]
        best_later = max(later) if later else 0
        if last:
            v = "play" if now_x > 0 else "save"
        elif now_x >= minimum_xp and now_x >= best_later - 0.3:
            v = "play"
        elif now_x >= minimum_xp and now_x >= best_later - 1.0:
            v = "consider"
        else:
            v = "save"
        return v, f"{label(0)} {now_x:.1f} xPts this week (best later {best_later:.1f})"

    if "Triple Captain" in avail:
        ks = per_gw(avail["Triple Captain"])
        caps = [max(squad, key=lambda i: xp_window[i][k]) for k in ks]
        extras = [xp_window[c][k] for c, k in zip(caps, ks)]
        label = lambda j: players[caps[j if j < len(caps) else 0]]["web_name"]
        pos = {k: n for n, k in enumerate(ks)}
        v, text = judge(ks, extras, TC_MIN, avail["Triple Captain"] == g0, lambda k: label(pos.get(k, 0)))
        advice.append({"chip": "Triple Captain", "verdict": v,
                       "reason": text + f" (chip expires after GW{avail['Triple Captain']})",
                       "short": f"Triple Captain on {label(0)} ({extras[0]:.1f} xPts)"})

    if "Bench Boost" in avail:
        ks = per_gw(avail["Bench Boost"])
        extras = []
        for k in ks:
            xg = {i: xp_window[i][k] for i in squad}
            extras.append(sum(xg[i] for i in lineup(players, xg, squad)["bench"]))
        v, text = judge(ks, extras, BB_MIN, avail["Bench Boost"] == g0, lambda k: "bench")
        advice.append({"chip": "Bench Boost", "verdict": v,
                       "reason": text + f" (chip expires after GW{avail['Bench Boost']})",
                       "short": f"Bench Boost (bench {extras[0]:.1f} xPts)"})

    if "Free Hit" in avail:
        xp1 = {i: [v[0]] for i, v in xp_window.items()}
        fh = optimise(players, xp1, squad, sell, bank, ft, None)
        gain = (fh["xp_next"] - base_xp) if fh else 0
        blanks = sum(1 for i in squad if not model.team_fixtures(g0).get(players[i]["team"]))
        last = avail["Free Hit"] == g0
        v = "play" if gain >= FH_MIN or (last and gain > 4) else "consider" if gain >= FH_MIN * 0.75 else "save"
        odd = [g for g in gws_window[1:] if g <= avail["Free Hit"] and _odd_week(model, g)]
        hint = f"; blank/double later in GW{', GW'.join(map(str, odd[:3]))} — better use there" if odd and v == "save" else ""
        h2h_note = f", {win_text(0, gain)}" if match(0) and gain > 0 else ""
        advice.append({"chip": "Free Hit", "verdict": v,
                       "reason": f"a one-week team would add {gain:+.1f} xPts ({blanks} of your players blank)"
                                 + h2h_note + hint,
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
