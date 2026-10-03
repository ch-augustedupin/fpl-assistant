"""Weekly FPL advice: transfers, captain, starting XI, bench and the H2H opponent preview.

    python fpl_report.py --team 3442422 --league 620050
    python fpl_report.py --override "Saka=50,Rice=0"     # late team news from press conferences
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fpl import api, h2h
from fpl.model import POS, Model, norm
from fpl.optimize import lineup, optimise, selling_price

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
FT_BANK_VALUE = 1.5  # points a saved free transfer is worth next week
HIT_MARGIN = 3      # extra predicted points a -4 hit must earn, since predictions are noisy

lines = []


def out(s=""):
    print(s)
    lines.append(s)


def m(tenths):
    return f"£{tenths / 10:.1f}m"


def parse_overrides(text, players):
    res = {}
    for part in filter(None, (t.strip() for t in (text or "").split(","))):
        name, pct = part.rsplit("=", 1)
        key = norm(name)
        hits = [p for p in players.values() if norm(p["web_name"]) == key] or \
               [p for p in players.values() if key in norm(p["first_name"] + p["second_name"])]
        if len(hits) != 1:
            sys.exit(f"--override: '{name}' matches {len(hits)} players "
                     f"({', '.join(p['web_name'] for p in hits[:6])}); be more specific")
        res[hits[0]["id"]] = int(pct)
    return res


def free_transfers(history, started):
    """FTs available for the next deadline: +1 per GW, max 5, chips (WC/FH) don't consume them."""
    chip_gws = {c["event"] for c in history["chips"] if c["name"] in ("wildcard", "freehit")}
    ft = 1
    for gw in history["current"]:
        e = gw["event"]
        if e <= started:
            continue
        if e not in chip_gws:
            ft = max(ft - gw["event_transfers"], 0)
        ft = min(5, ft + 1)
    return ft


def purchase_prices(squad, transfers, players):
    bought = {}
    for t in sorted(transfers, key=lambda t: t["time"]):
        bought[t["element_in"]] = t["element_in_cost"]
    return {pid: bought.get(pid, players[pid]["now_cost"] - players[pid]["cost_change_start"]) for pid in squad}


def chips_available(boot, history, gw):
    used = [(c["name"], c["event"]) for c in history["chips"]]
    names = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
    avail = []
    for c in boot["chips"]:
        if c["start_event"] <= gw <= c["stop_event"] and not any(
                n == c["name"] and c["start_event"] <= e <= c["stop_event"] for n, e in used):
            avail.append(names.get(c["name"], c["name"]))
    return avail


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", type=int, default=CONFIG["team_id"])
    ap.add_argument("--league", type=int, default=CONFIG["league_id"])
    ap.add_argument("--horizon", type=int, default=5, help="gameweeks to plan over")
    ap.add_argument("--max-transfers", type=int, default=2)
    ap.add_argument("--ft", type=int, help="override the computed number of free transfers")
    ap.add_argument("--override", help='availability overrides, e.g. "Saka=50,Rice=0" (percent)')
    ap.add_argument("--exclude", help="comma-separated players never to buy")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    boot = api.bootstrap()
    players = {p["id"]: p for p in boot["elements"]}
    short = {t["id"]: t["short_name"] for t in boot["teams"]}
    gw = api.current_gw(boot)
    nxt = next(e for e in boot["events"] if e["is_next"])
    next_gw = nxt["id"]
    gws = list(range(next_gw, min(38, next_gw + args.horizon - 1) + 1))
    fixtures = api.fixtures()
    hist = api.player_histories(boot, gw)
    overrides = parse_overrides(args.override, players)
    banned = set(parse_overrides(",".join(f"{n}=0" for n in (args.exclude or "").split(",") if n), players))
    model = Model(boot, fixtures, hist, cutoff=gw, overrides=overrides, europe_teams=CONFIG["europe_teams"])
    xp = model.predict(gws)
    xp1 = {pid: v[0] for pid, v in xp.items()}

    ent = api.entry(args.team)
    ehist = api.entry_history(args.team)
    picks = api.entry_picks(args.team, gw)
    if picks["active_chip"] == "freehit":  # the Free Hit squad reverts after the gameweek
        picks = {**api.entry_picks(args.team, gw - 1), "entry_history": picks["entry_history"]}
    squad = [p["element"] for p in picks["picks"]]
    bank = picks["entry_history"]["bank"]
    bought = purchase_prices(squad, api.entry_transfers(args.team), players)
    sell = {pid: selling_price(players[pid]["now_cost"], bought[pid]) for pid in squad}
    ft = args.ft if args.ft is not None else free_transfers(ehist, ent["started_event"])

    league_sq = h2h.league_squads(args.league, gw)
    others = {e: s for e, s in league_sq.items() if e != args.team}
    lown = {pid: sum(pid in s for s in others.values()) / max(1, len(others)) for pid in players}
    opp = h2h.opponent(args.league, next_gw, args.team)

    def name(pid):
        p = players[pid]
        return f"{p['web_name']} ({short[p['team']]} {POS[p['element_type']]} {m(p['now_cost'])})"

    deadline = datetime.fromisoformat(nxt["deadline_time"].replace("Z", "+00:00"))
    out(f"# FPL report — {ent['name']} ({ent['player_first_name']} {ent['player_last_name']})")
    out(f"GW{next_gw} deadline: {deadline:%a %d %b %H:%M} UTC  ·  generated "
        f"{datetime.now(timezone.utc):%d %b %H:%M} UTC  ·  data up to GW{gw}")
    out(f"Points {ent['summary_overall_points']}  ·  bank {m(bank)}  ·  free transfers {ft}  ·  "
        f"planning GW{gws[0]}–GW{gws[-1]}")
    if overrides:
        out("Overrides: " + ", ".join(f"{players[p]['web_name']}={v}%" for p, v in overrides.items()))
    out()

    # ---- current squad
    out("## Current squad (xPts next GW / next %d GWs)" % len(gws))
    for pid in sorted(squad, key=lambda i: (players[i]["element_type"], -xp1[i])):
        flags = model.flags(pid, next_gw)
        out(f"- {name(pid):34} {xp1[pid]:4.1f} / {sum(xp[pid]):5.1f}  {model.rates[pid]['trend']}  "
            f"{model.fixture_text(players[pid]['team'], gws)}" + (f"  [{'; '.join(flags)}]" if flags else ""))
    out()

    # ---- transfer plans
    plans = {}
    for k in range(0, args.max_transfers + 1):
        plan = optimise(players, xp, squad, sell, bank, ft, k, banned=banned)
        if plan:
            next_ft = min(5, max(ft - k, 0) + 1)
            plan["score"] = plan["objective"] + FT_BANK_VALUE * (next_ft - 1) - HIT_MARGIN * plan["hits"] / 4
            plans[k] = plan
    wc = optimise(players, xp, squad, sell, bank, ft, None, banned=banned)
    base = plans[0]["objective"]
    best_k = max(plans, key=lambda k: plans[k]["score"])
    out("## Transfer options (gain = weighted xPts over the horizon vs. no transfer, after hits)")
    for k, plan in plans.items():
        tag = "  ← RECOMMENDED" if k == best_k else ""
        if k == 0:
            out(f"- **Roll the transfer** (bank 1 FT → {min(5, ft + 1)} next week){tag}")
            continue
        hit = f", hit -{plan['hits']}" if plan["hits"] else ""
        out(f"- **{k} transfer{'s' if k > 1 else ''}**: gain {plan['objective'] - base:+.1f}{hit}, "
            f"bank after {m(plan['bank_after'])}{tag}")
        for o, i in zip(plan["out"], plan["in"]):
            out(f"    OUT {name(o)}  sell {m(sell[o])}  xPts {sum(xp[o]):.1f}")
            flags = model.flags(i, next_gw)
            own = f"league owners {lown[i]:.0%}" + (" — differential" if lown[i] < 0.2 else "")
            out(f"    IN  {name(i)}  xPts {sum(xp[i]):.1f}  ·  {model.reason(i, gws)}  ·  {own}"
                + (f"  [{'; '.join(flags)}]" if flags else ""))
    out()

    # ---- line-up
    rec = plans[best_k]
    lu = lineup(players, xp1, rec["squad"])
    out(f"## GW{next_gw} line-up (with the recommended transfers)  —  predicted {lu['xp']:.1f} pts")
    out(f"Captain: {name(lu['captain'])} {xp1[lu['captain']]:.1f} xPts  ·  "
        f"Vice: {name(lu['vice'])} {xp1[lu['vice']]:.1f}")
    for pos in (1, 2, 3, 4):
        for pid in [i for i in lu["xi"] if players[i]["element_type"] == pos]:
            flags = model.flags(pid, next_gw)
            out(f"  {name(pid):34} {xp1[pid]:4.1f}  {model.reason(pid, gws)}"
                + (f"  [{'; '.join(flags)}]" if flags else ""))
    out("Bench (in order): " + ", ".join(f"{players[i]['web_name']} {xp1[i]:.1f}" for i in lu["bench"]))
    out()

    # ---- H2H
    if opp:
        opp_id, opp_name, opp_mgr = opp
        prev = h2h.preview(players, xp1, lu["xi"], lu["captain"], others.get(opp_id) or
                           [p["element"] for p in api.entry_picks(opp_id, gw)["picks"]])
        o = prev["opp"]
        out(f"## H2H GW{next_gw}: vs {opp_name} ({opp_mgr})")
        out(f"Predicted {prev['my_xp']:.1f} – {o['xp']:.1f}  ·  rough win chance {prev['p_win']:.0%}  "
            f"(their squad as of GW{gw}; their new transfers are hidden until the deadline)")
        out(f"Their likely captain: {name(o['captain'])}  ·  shared starters (cancel out): "
            + (", ".join(players[i]["web_name"] for i in prev["shared"]) or "none"))
        out("Your differentials: " + ", ".join(f"{players[i]['web_name']} {xp1[i]:.1f}" for i in prev["my_diff"]))
        out("Their differentials: " + ", ".join(f"{players[i]['web_name']} {xp1[i]:.1f}" for i in prev["their_diff"]))
        if lu["captain"] in o["xi"]:
            note = "they also start him" + (" and will probably captain him too" if o["captain"] == lu["captain"] else "")
            out(f"Captain note: {players[lu['captain']]['web_name']} — {note}; the gain only counts if they don't captain him.")
            if prev["p_win"] < 0.45:
                alts = [i for i in lu["xi"] if i not in o["xi"] and xp1[i] >= xp1[lu["captain"]] - 2]
                if alts:
                    out(f"You're projected to lose: a differential captain like {players[alts[0]]['web_name']} "
                        f"({xp1[alts[0]]:.1f}) adds upside.")
        out()

    # ---- chips
    avail = chips_available(boot, ehist, next_gw)
    out("## Chips")
    out("Available: " + (", ".join(avail) or "none"))
    for g in gws:
        tf = model.team_fixtures(g)
        dbl = [short[t] for t, f in tf.items() if len(f) > 1]
        blank = [short[t] for t in short if t not in tf]
        if dbl:
            out(f"GW{g} double gameweek: {', '.join(dbl)}")
        if blank:
            out(f"GW{g} blank: {', '.join(blank)}")
    bench_xp = sum(xp1[i] for i in lu["bench"])
    if "Bench Boost" in avail and bench_xp >= 12:
        out(f"Bench Boost worth considering this week: bench predicted {bench_xp:.1f} pts.")
    if "Triple Captain" in avail and xp1[lu["captain"]] >= 11:
        out(f"Triple Captain worth considering: {players[lu['captain']]['web_name']} {xp1[lu['captain']]:.1f} xPts.")
    if wc and "Wildcard" in avail:
        gain = wc["objective"] - plans[best_k]["objective"]
        out(f"Wildcard squad would add {gain:+.1f} weighted xPts over the horizon "
            + ("— worth considering." if gain >= 15 else "— not needed now."))
    out()
    out("xPts = expected points from form (recent GWs weigh most), minutes/rotation, injury flags and fixtures. "
        "Check late press-conference news before the deadline.")

    # ---- short summary for WhatsApp / the email subject block
    pn = lambda i: players[i]["web_name"]
    bogota = deadline - timedelta(hours=5)  # Colombia is UTC-5 all year
    summary = [f"⚽ FPL GW{next_gw} — {ent['name']}",
               f"Deadline: {bogota:%a %d %b %H:%M} Bogotá"]
    if best_k == 0:
        summary.append(f"Transfers: none — roll it ({min(5, ft + 1)} FTs next week)")
    else:
        moves = ", ".join(f"{pn(o)} → {pn(i)}" for o, i in zip(rec["out"], rec["in"]))
        hit = f", hit -{rec['hits']}" if rec["hits"] else ""
        summary.append(f"Transfers: {moves} (+{rec['objective'] - base:.1f} xPts over {len(gws)} GWs{hit})")
    summary.append(f"Captain: {pn(lu['captain'])} ({xp1[lu['captain']]:.1f}) · Vice: {pn(lu['vice'])}")
    summary.append("Bench: " + ", ".join(pn(i) for i in lu["bench"]))
    if opp:
        summary.append(f"H2H vs {opp_name}: {prev['my_xp']:.0f}–{o['xp']:.0f} predicted ({prev['p_win']:.0%} win)")
    warn = [f"{pn(i)} {players[i]['chance_of_playing_next_round']}%" for i in rec["squad"]
            if players[i]["status"] in ("d", "i", "s") or i in overrides]
    if warn:
        summary.append("⚠ Doubtful: " + ", ".join(warn))
    if wc and "Wildcard" in avail and wc["objective"] - plans[best_k]["objective"] >= 15:
        summary.append(f"Chip idea: Wildcard (+{wc['objective'] - plans[best_k]['objective']:.0f} xPts)")
    summary.append("Full report in your email. Check press conferences before the deadline.")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"GW{next_gw}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (reports / f"GW{next_gw}.summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return next_gw


if __name__ == "__main__":
    main()
