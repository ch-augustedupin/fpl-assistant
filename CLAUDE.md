# CLAUDE.md

Weekly Fantasy Premier League advice for the owner's team "Vodka Juniors" (entry **3442422**) in the private H2H
league "Fantasy PL 26/27" (**620050**, 18 friends). Python, no build step. IDs and Europe teams live in `config.json`.

## Commands
```bash
pip install -r requirements.txt
python fpl_report.py                              # report for the next deadline, also saved to reports/GW{n}.md
python fpl_report.py --override "Saka=50,Rice=0"  # late press-conference news (percent chance of playing)
python fpl_report.py --exclude "Haaland" --max-transfers 3 --horizon 6 --ft 2
python backtest.py --gws 3 4 5                    # model vs naive form on finished GWs
```

## Weekly routine (in a Claude Code session, ~2 days before the deadline)
1. Run the report. 2. Web-search pre-match press conferences for flagged/doubtful players in the squad and the
transfer targets. 3. Rerun with `--override` for anything newer than the FPL `news` field. 4. Summarise for the user.
The user makes transfers on the FPL site themselves — this tool never logs in.

## Data (public API, no login): `fpl/api.py`
`bootstrap-static/`, `fixtures/`, `element-summary/{id}/` (one per player, ~570 calls ≈ 2–3 min, cached in `cache/`
per finished GW), `entry/{id}/`, `/history/`, `/transfers/`, `/event/{gw}/picks/`, `leagues-h2h/{id}/standings/`,
`leagues-h2h-matches/league/{id}/?event={gw}`. Opponents' new transfers are hidden until the deadline.
- 2026/27 quirk: `teams[].strength_attack_*` / `strength_defence_*` are all 0, so team strength is computed from
  fixtures (75% xG + 25% goals, shrunk to `strength_overall_*`).
- Scoring comes from `game_config.scoring` (includes defensive contribution: DEF 10 CBIT, MID/FWD 12 CBIRT → 2 pts).
- Selling price = purchase + half the profit (floor). Purchase price from `/transfers/`, else `now_cost - cost_change_start`.

## Model (`fpl/model.py`)
- Per-90 rates (xG, xA, saves, bonus, yellows, DC hit rate) = recency-weighted this season (DECAY 0.8 per GW)
  + last season (≤ 8 games' weight) + a small position prior.
- Minutes: P(start), P(sub), minutes when starting from the last 6 GWs; × chance of playing (status/news/overrides);
  injured players recover gradually over later GWs. Europe clubs (config) get a small rotation penalty if not nailed.
- Fixture: team λ = mu × attack × opponent defence × home advantage 1.10; CS = exp(-λ_opp).
- Flags: injury news, rotation risk, often subbed, 1 yellow from a ban (4 YC by GW19, 9 by GW32), price change / net transfers.
- Backtest (GW3–5): correlation 0.41–0.45 vs 0.25–0.39 for naive form; MAE ~1.8 vs ~2.1.

## Optimiser (`fpl/optimize.py`)
PuLP integer program over the horizon (decay 0.85): transfers now, XI + captain re-picked each GW, bench weight 0.1,
≤3 per club, budget with selling prices. Plans for 0..N transfers + a wildcard plan. The report recommends the best
score = objective + 1.5 per banked FT − 3 extra per −4 hit (hits need a clear margin). Hard asserts on squad legality.
- PuLP is pinned `<4`: 4.0 changed the `LpVariable` API and ships no solver (CBC is bundled with 3.x).

## Git
Repo-scoped identity `ch-augustedupin <ch-augustedupin@users.noreply.github.com>`. PUBLIC repo https://github.com/ch-augustedupin/fpl-assistant (branch main). The user wants no league members' real names
and no transfer plans public: reports/ is git-ignored, notify.py captures the report output so Actions logs stay clean,
and the README example is made up. Keep it that way. The bot commits `state/sent.txt` — `git pull` before editing.

## Delivery (`notify.py` + `.github/workflows/weekly-report.yml`)
- Actions runs every 2 h; `notify.py` sends once per GW when the deadline is 2–38 h away (Saturday 10:00 UTC
  deadline → Thursday evening Bogotá). `state/sent.txt` lists GWs already sent; the bot commits only that.
- Email: Resend API if `RESEND_API_KEY` is set (from onboarding@resend.dev to the account owner's address; Gmail
  refused SMTP logins from GitHub runners with the correct app password), else Gmail SMTP with an app password (secrets `GMAIL_USER`, `GMAIL_APP_PASSWORD`, optional `MAIL_TO`).
  Full report as HTML with the summary box on top. WhatsApp: CallMeBot (`WHATSAPP_PHONE` with country code,
  `CALLMEBOT_APIKEY`), summary only (`reports/GW{n}.summary.txt`, ~400 chars).
- Test: `python notify.py --force --dry-run` locally, or Actions → "Run workflow" with force.
