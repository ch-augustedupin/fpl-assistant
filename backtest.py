"""Backtest: predict each of the last finished gameweeks using only earlier data, compare with real points.

    python backtest.py            # last 2 finished GWs
    python backtest.py --gws 3 4 5

Injury flags are not available historically, so the model runs without them here (it is slightly
better in real use). Baseline = the "form" most players eyeball: average points over the last 4 GWs.
"""
import argparse
import statistics
import sys

from fpl import api
from fpl.model import Model


def corr(a, b):
    return statistics.correlation(a, b) if len(a) > 2 else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gws", type=int, nargs="*")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    boot = api.bootstrap()
    gw = api.current_gw(boot)
    fixtures = api.fixtures()
    hist = api.player_histories(boot, gw)
    targets = args.gws or [gw - 1, gw]
    print(f"{'GW':>3} {'players':>7} | {'model r':>7} {'MAE':>5} | {'form r':>6} {'MAE':>5} | top-30 picks avg pts: model / form")
    for t in targets:
        model = Model(boot, fixtures, hist, cutoff=t - 1, use_flags=False)
        pred = model.predict([t])
        rows = []
        for pid, h in hist.items():
            played_before = [r for r in h["history"] if r["round"] < t]
            now = [r for r in h["history"] if r["round"] == t]
            if not now or not any(r["minutes"] for r in played_before):
                continue
            actual = sum(r["total_points"] for r in now)
            last4 = sorted(played_before, key=lambda r: r["round"])[-4:]
            form = sum(r["total_points"] for r in last4) / len(last4)
            rows.append((pred[pid][0], form, actual))
        p, f, a = zip(*rows)
        mae = lambda x: sum(abs(i - j) for i, j in zip(x, a)) / len(a)
        top = lambda k: sum(r[2] for r in sorted(rows, key=lambda r: -r[k])[:30]) / 30
        print(f"{t:>3} {len(rows):>7} | {corr(p, a):7.3f} {mae(p):5.2f} | {corr(f, a):6.3f} {mae(f):5.2f} | "
              f"{top(0):.2f} / {top(1):.2f}")


if __name__ == "__main__":
    main()
