# FPL Assistant

Weekly Fantasy Premier League advice for my team, **Vodka Juniors**, in our private head-to-head league
"Fantasy PL 26/27". About 36 hours before every deadline it works out the best transfers, captain, starting XI and
bench, previews this week's H2H opponent, and sends the result to me:

- **WhatsApp** and **Telegram**: a short summary (Telegram also attaches the full report)
- **Email**: the full report

It uses only the public Fantasy Premier League API (no login) and never changes the team. I make the transfers myself.

## What the report contains

| Section | What it tells me |
|---|---|
| Current squad | Expected points (xPts) for every player next GW and over the next 5, form trend ↑/→/↓, next fixtures, warnings |
| Transfer options | Best plan with 0, 1 and 2 transfers: who to sell and buy, the points gained, the bank left, and the reasons |
| Line-up | Starting XI, captain, vice-captain and bench order for the next gameweek |
| H2H preview | This week's opponent, predicted score, rough win chance, shared players and each side's differentials |
| Chips | For each chip you still have: ✅ play this week / 🤔 worth considering / ⏳ save. Triple Captain and Bench Boost are compared with every later gameweek of the chip's window (GW1–19 or GW20–38); Free Hit and Wildcard by how much a rebuilt team would add. Because the goal is winning the H2H league, chips are judged by the **league points** they add: for every week it looks at who you play, how close the match is predicted to be, and whether the opponent is a direct rival in the table (a chip matters most in a close match against a rival). Also shows which chips your opponent still has |

Example WhatsApp message (made-up data):

```
⚽ FPL GW12 — My Team
Deadline: Sat 21 Nov 08:30 Bogotá
Transfers: Player A → Player B (+4.8 xPts over 5 GWs)
Captain: Player C (7.1) · Vice: Player D
Bench: GK2, Player E, Player F, Player G
H2H vs Rival FC: 58–51 predicted (62% win)
⚠ Doubtful: Player E 75%
```

## How it decides

**Expected points.** For every player and gameweek, the model combines:

- **Form**: expected goals (xG) and expected assists (xA), plus saves, bonus points and defensive contributions,
  per 90 minutes. Recent gameweeks count most, blended with last season so one big haul doesn't dominate.
- **Condition**: the injury or suspension status and the "chance of playing" from FPL. Injured players come back
  gradually over the following weeks.
- **Minutes and rotation**: how often the player has started lately, how long they stay on, and whether their club
  plays in Europe midweek.
- **Fixtures**: how strong each team's attack and defence are, measured from this season's xG and goals, plus
  home advantage. Double and blank gameweeks are handled.
- **Warnings**: one yellow card from a ban, and price rises or falls likely before the deadline.

**Choosing the team.** An optimiser picks the transfers that maximise expected points over the next 5 gameweeks.
It respects every FPL rule: the budget at real selling prices, 2 GK, 5 DEF, 5 MID and 3 FWD, at most 3 players per
club, valid formations, and −4 for each extra transfer. It only recommends a points hit when the gain is clearly
larger than the cost. It also values saving a free transfer for the next week.

**Does it work?** It was tested on gameweeks 3–5, predicting each one only from the data before it. It beat the
usual "average of the last few gameweeks" approach every time:

| | Model | Recent-points average |
|---|---|---|
| Correlation with actual points | 0.41–0.45 | 0.25–0.39 |
| Average error (points per player) | ~1.8 | ~2.1 |

FPL is still very noisy, so the predictions improve the odds but guarantee nothing.

## Automatic delivery

A GitHub Actions workflow (`.github/workflows/weekly-report.yml`) checks every hour and sends two messages per
gameweek:

1. **Main report**, about 36 hours before the deadline (Thursday afternoon/evening Bogotá for Saturday deadlines):
   the full report by email and the summary by WhatsApp.
2. **Friday update**, about 12 hours before the deadline, after the managers' press conferences. The report is
   rebuilt with the latest injury flags and compared with Thursday's. If the transfers, captain, line-up or a squad
   player's status changed, WhatsApp lists what changed and the email brings the new full report. Otherwise a single
   WhatsApp line says "no changes".

Each message and channel is sent once (recorded in `state/sent.txt`). A channel that fails — the free WhatsApp relay
is sometimes down — is retried on every later run until the deadline. Reports are never committed or printed in the
Actions log, because the repo is public: transfer plans and league members' names stay in the private email and
WhatsApp only. To compare with Thursday, only a keyed fingerprint of the decisions is stored
(`state/GW{n}.fingerprint.json`); it can't be read back without the repository secrets.

Repository secrets:

| Secret | Purpose |
|---|---|
| `RESEND_API_KEY` | Sends the email through [Resend](https://resend.com) (free plan) |
| `GMAIL_USER` | The address that receives the email (`MAIL_TO` can override it) |
| `WHATSAPP_PHONE`, `CALLMEBOT_APIKEY` | WhatsApp message through [CallMeBot](https://www.callmebot.com) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram message from your own bot — set both with `python telegram_setup.py` |

To send a report now, go to Actions → "FPL report before each deadline" → **Run workflow** and tick *force*. A
forced test run does not count as the gameweek's real send. Locally, `python notify.py --force --phase friday --dry-run`
prints the Friday update.

## Running it locally

```bash
pip install -r requirements.txt
python fpl_report.py                              # report for the next deadline (saved in reports/, git-ignored)
python fpl_report.py --override "Saka=50,Rice=0"  # late injury news from press conferences (% chance of playing)
python fpl_report.py --exclude "Haaland" --max-transfers 3 --horizon 6
python notify.py --force --dry-run                # build the messages without sending
python backtest.py --gws 3 4 5                    # check the model against past gameweeks
```

The first run downloads every player's history (about 2–3 minutes). It is cached until the next gameweek finishes.

## Files

| File | Role |
|---|---|
| `fpl_report.py` | Builds the weekly report and the short summary |
| `notify.py` | Checks the timing and sends the email and WhatsApp |
| `fpl/api.py` | Public FPL API client with caching |
| `fpl/model.py` | Expected-points model: form, condition, minutes, fixtures |
| `fpl/optimize.py` | Transfer and line-up optimiser (PuLP) |
| `fpl/h2h.py` | Head-to-head opponent preview and league ownership |
| `fpl/chips.py` | Chip advisor: is this a good week for each remaining chip |
| `telegram_setup.py` | One-time Telegram setup (saves the bot secrets in GitHub) |
| `backtest.py` | Tests the model against finished gameweeks |
| `config.json` | Team ID, league ID and the clubs playing in Europe this season |

## Limitations

- The opponent's new transfers are hidden until the deadline, so the H2H preview uses their squad from last week.
- FPL's injury news can lag behind managers' press conferences. Before the deadline, check the news and rerun with
  `--override` (or ask Claude Code to do it).
- The list of clubs playing in Europe is set by hand in `config.json` for each season.

Built with [Claude Code](https://claude.com/claude-code).
