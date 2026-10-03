"""Squad optimiser: integer program over the next N gameweeks (PuLP + HiGHS).

Transfers are made now; the squad is then held for the horizon while the XI and captain are re-picked
every gameweek. Later gameweeks are discounted because predictions get less reliable.
"""
import pulp


def binaries(prefix, keys):
    return {k: pulp.LpVariable(f"{prefix}_{'_'.join(map(str, k)) if isinstance(k, tuple) else k}", cat="Binary")
            for k in keys}

SQUAD = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
HIT = 4
BENCH_WEIGHT = 0.1  # bench players still matter a bit (auto-subs)


def selling_price(now, bought):
    """FPL rule: you keep half the profit (rounded down to £0.1m), all of a loss."""
    return now if now <= bought else bought + (now - bought) // 2


def solver():
    return pulp.HiGHS(msg=False) if "HiGHS" in pulp.listSolvers(onlyAvailable=True) else pulp.PULP_CBC_CMD(msg=False)


def optimise(players, xp, squad, sell, bank, free_transfers, n_transfers, decay=0.85, pool_size=40, banned=()):
    """
    players: {pid: bootstrap element}; xp: {pid: [xPts per GW]}; squad: current 15 pids;
    sell: {pid: selling price (tenths)} for the squad; bank in tenths.
    Exactly `n_transfers` transfers (0 = keep the squad; None = wildcard, unlimited and free).
    Returns a plan dict or None if infeasible.
    """
    wildcard = n_transfers is None
    horizon = len(next(iter(xp.values())))
    weights = [decay ** k for k in range(horizon)]
    value = {pid: sum(w * v for w, v in zip(weights, xp[pid])) for pid in xp}
    # candidate pool: best players per position by horizon value, plus the current squad
    pool = set(squad)
    for pos in SQUAD:
        cands = [pid for pid, p in players.items() if p["element_type"] == pos and p["can_transact"]
                 and p["status"] != "u" and pid not in banned]
        pool |= set(sorted(cands, key=lambda pid: -value[pid])[:pool_size])
    pool = sorted(pool)
    G = range(horizon)
    cur = set(squad)

    prob = pulp.LpProblem("fpl", pulp.LpMaximize)
    x = binaries("squad", pool)
    s = binaries("xi", [(i, g) for i in pool for g in G])
    c = binaries("cap", [(i, g) for i in pool for g in G])

    prob += pulp.lpSum(weights[g] * xp[i][g] * (s[i, g] + c[i, g] + BENCH_WEIGHT * (x[i] - s[i, g]))
                       for i in pool for g in G) - (0 if wildcard else HIT * max(0, n_transfers - free_transfers))

    for pos, n in SQUAD.items():
        prob += pulp.lpSum(x[i] for i in pool if players[i]["element_type"] == pos) == n
    for team in {players[i]["team"] for i in pool}:
        prob += pulp.lpSum(x[i] for i in pool if players[i]["team"] == team) <= 3
    if not wildcard:
        prob += pulp.lpSum(x[i] for i in cur) == 15 - n_transfers
    buy = pulp.lpSum(players[i]["now_cost"] * x[i] for i in pool if i not in cur)
    sold = pulp.lpSum(sell[i] * (1 - x[i]) for i in cur)
    prob += buy - sold <= bank
    for g in G:
        prob += pulp.lpSum(s[i, g] for i in pool) == 11
        prob += pulp.lpSum(c[i, g] for i in pool) == 1
        for pos in SQUAD:
            n_xi = pulp.lpSum(s[i, g] for i in pool if players[i]["element_type"] == pos)
            prob += n_xi >= XI_MIN[pos]
            prob += n_xi <= XI_MAX[pos]
        for i in pool:
            prob += s[i, g] <= x[i]
            prob += c[i, g] <= s[i, g]

    prob.solve(solver())
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    new = [i for i in pool if x[i].value() > 0.5]
    xi_next = [i for i in pool if s[i, 0].value() > 0.5]
    out_ = sorted(cur - set(new))
    in_ = sorted(set(new) - cur)
    cost_in = sum(players[i]["now_cost"] for i in in_)
    gain_out = sum(sell[i] for i in out_)
    _check(players, new, xi_next)
    return {"squad": new, "xi": xi_next, "out": out_, "in": in_,
            "bank_after": bank - cost_in + gain_out,
            "hits": 0 if wildcard else HIT * max(0, n_transfers - free_transfers),
            "objective": pulp.value(prob.objective),
            "xp_next": sum(xp[i][0] for i in xi_next) + max(xp[i][0] for i in xi_next)}


def _check(players, squad, xi):
    """Hard sanity checks so a solver/modelling bug can never produce an illegal team."""
    assert len(squad) == 15 and len(xi) == 11
    for pos, n in SQUAD.items():
        assert sum(players[i]["element_type"] == pos for i in squad) == n
        k = sum(players[i]["element_type"] == pos for i in xi)
        assert XI_MIN[pos] <= k <= XI_MAX[pos]
    teams = [players[i]["team"] for i in squad]
    assert max(teams.count(t) for t in set(teams)) <= 3


def lineup(players, xp_next, squad):
    """Best XI, captain, vice and bench order for one gameweek from a fixed 15."""
    by_xp = sorted(squad, key=lambda i: -xp_next[i])
    prob = pulp.LpProblem("xi", pulp.LpMaximize)
    s = binaries("s", squad)
    prob += pulp.lpSum(xp_next[i] * s[i] for i in squad)
    prob += pulp.lpSum(s.values()) == 11
    for pos in SQUAD:
        n = pulp.lpSum(s[i] for i in squad if players[i]["element_type"] == pos)
        prob += n >= XI_MIN[pos]
        prob += n <= XI_MAX[pos]
    prob.solve(solver())
    xi = [i for i in by_xp if s[i].value() > 0.5]
    bench = [i for i in by_xp if i not in xi]
    gk_bench = [i for i in bench if players[i]["element_type"] == 1]
    bench = gk_bench + [i for i in bench if i not in gk_bench]
    return {"xi": xi, "captain": xi[0], "vice": xi[1], "bench": bench,
            "xp": sum(xp_next[i] for i in xi) + xp_next[xi[0]]}
