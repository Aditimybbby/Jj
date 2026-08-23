# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

LazyFarmers ("neura-self") is a multi-account Discord **selfbot** that automates the OwO bot game, plus a
Flask web dashboard for monitoring and configuring it. Python 3.10+, `discord.py-self` (pinned commit),
no build step, no test suite.

## Commands

```bash
# Run everything (bot instances + dashboard on http://localhost:8000, or $PORT)
python neura.py

# Same, skipping the interactive rich menu (auto-starts enabled accounts)
LAZYFARMERS_HEADLESS=1 python neura.py

# Installer + terminal account/proxy manager (also reachable as option 2 in neura.py)
python neura_setup.py

# Dependencies (discord.py-self must be the pinned commit; setup_engine verifies the
# version suffix "+g20ae80b3" and force-reinstalls if wrong)
pip install -r requirements.txt
```

There are no tests and no linter config. The de facto verification step is a syntax/import check:

```bash
python -m compileall -q core cogs modules utils neura_engines component_v2_neura dashboard neura.py neura_setup.py
```

Beyond that, changes are validated by running the app and watching the rich console log / dashboard.

Environment variables: `LAZYFARMERS_DATA_ROOT` (persistent volume), `RAILWAY_VOLUME_MOUNT_PATH` (same,
auto-set by Railway), `LAZYFARMERS_HEADLESS`, `LAZYFARMERS_DASHBOARD_USER`,
`LAZYFARMERS_DASHBOARD_PASSWORD`, `PORT`. Deployment: `Procfile` + `railway.json` + `nixpacks.toml`
(see `RAILWAY.md`); must stay at 1 replica or every account sends each command twice.

## Architecture

### Process shape

`neura.py` → runs `NeuraSetupEngine.environment_healthy()` (self-installs deps and re-execs if not) →
starts Flask in a **daemon thread** → optional interactive menu → `supervisor.start_all(...)` → idles
forever. Headless is auto-detected (`LAZYFARMERS_HEADLESS`, any `RAILWAY_*` env var, or no tty) because
a blocked console prompt would otherwise keep the dashboard from ever serving.

One `NeuraBot` (`core/bot.py`, a `commands.Bot` with `self_bot=True`) per Discord account. All live
instances are in `core.state.bot_instances`. `core/supervisor.py` is the only place bots are created or
destroyed; the dashboard drives it so accounts can be started/stopped without restarting the process.

**Threading rule:** Flask handlers run on a different thread than the asyncio loop. Anything awaitable
must be scheduled with `asyncio.run_coroutine_threadsafe(coro, bot.loop)` or the `_bot_loop_call` /
`_bot_loop_fire` helpers in `dashboard/app.py` (they use the loop registered by
`supervisor.bind_loop()`). Setting plain flags like `bot.paused` from a route is fine.

### Command pipeline (the core abstraction)

Cogs almost never send messages directly. Instead:

1. `register_actions()` on a cog calls `bot.neura_register_command(cmd_id, content, priority, delay,
   initial_offset)`, which writes an entry into `bot.cmd_states`.
2. `neura_scheduler_worker` (1 s tick) sees `now - last_ran >= delay` and pushes onto
   `bot.neura_queue` (an `asyncio.PriorityQueue`).
3. `neura_queue_worker` pops, re-checks the per-`cmd_id` cooldown, then calls `_send_safe`.
4. `_send_safe` enforces warmup, `paused`, `throttle_until`, `min_command_interval`, resolves the
   channel, and sends — through `NeuraHuman.neura_send` (`modules/neura_human.py`) when stealth typing
   is on, which also owns the periodic "human break".

Details that matter when adding a command module:

- `content` may be a **callable** (sync or async); returning `None` skips that tick.
- `content == ""` means "timer only" — the worker calls a cog hook instead of sending (e.g.
  `channelswitch` → `ChannelSwitch.trigger_switch()`).
- After a send, the queue worker calls back into the owning cog (`trigger_action`, `trigger_coinflip`,
  `trigger_slots`, `trigger_blackjack`) so it can recompute the next `content`/`delay`. That mapping is
  a hardcoded `class_map` in `neura_queue_worker`.
- Priorities come from `config/cmd_priorities.json` via `bot.get_cmd_priority` (lower = more urgent;
  `owo` is 1, quest-engine work is 4–5).
- `_fix_command` normalizes the prefix and swaps in short forms from `config/shortform.json` when
  `commands.<name>.use_shortform` is set.
- Ad-hoc, out-of-schedule commands go through `bot.neura_enqueue(...)`; the dashboard's manual command
  box and captcha answers use `bot.send_message(..., priority=True)`.

### Config layering and live reload

`config/settings.json` is the global default. On first ready, each account writes and thereafter reads
`config/settings_<user_id>.json`, deep-merged **over** the global (`NeuraBot._load_config`).

`POST /api/settings` writes the file(s) then calls `bot.sync_settings(new_config)`, which diffs dotted
paths and refreshes *only* the affected cogs. Three maps in `core/bot.py` drive that:

- `_collect_changed_paths` — dotted diff of old vs new config
- `_cogs_for_config_changes` — command name / top-level section → cog class name
- `_prune_disabled_scheduler_cmds` — drops `cmd_states` entries whose config was just disabled

**When you add a new config section or command, update those maps**, or the setting will only take
effect on restart.

### Persistence

`core/paths.py` resolves `DATA_ROOT` (env volume or repo root), derives `CONFIG_DIR` / `DATA_DIR`, and
copies bundled `config/*` defaults into the volume on first boot. Re-exported through `core.state`, so
use `state.CONFIG_DIR` / `state.DATA_DIR` — hardcoding `config/` writes to the ephemeral repo copy
instead of the volume.

`core/state.py` holds the shared mutable state: `bot_instances`, `account_stats` (keyed by Discord user
id **string**), the `command_logs` deque the dashboard renders, and the `checking_gems` /
`missing_gems_cache` coordination dicts. `utils/history_tracker.py` persists sessions and cash to SQLite
at `DATA_DIR/neura_history.db` (it migrates a legacy `history.json` on import).

**`state.log_command` is load-bearing.** It is both the log sink and the stats counter: it parses log
*message text* (`"Sent: owo hunt"`, `"captcha solved"`, `"ban detected"`, …) to increment counters and
write history rows. Rewording a `bot.log(...)` string can silently break dashboard stats.

### Message handling

Every cog filters `on_message` the same way: author id == `core.monitor_bot_id` (the OwO bot,
`408785106942164992`), channel in `bot.channels`, then `bot.is_message_for_me(message)`. That last check
lives in `modules/identity.py` and matches username / display name / guild nick / mention, with
`role="header" | "source" | "target"` variants for "X's zoo!" headers and "A prays for B" directionality.
OwO frequently replies without a mention, so identity matching is the only thing keeping one account
from reacting to another player's messages.

Pause/throttle model: `bot.paused` (indefinite — Security cog or dashboard), `bot.throttle_until`
(timestamp; `float('inf')` means "until a captcha is solved"), `bot.warmup_until`. `_send_safe` blocks
on all three. `cogs/cooldown_manager.py` parses OwO's "slow down" replies (relative `<t:...:r>`
timestamps or "N seconds") to back-date `cmd_states[...]['last_ran']` and set `throttle_until`.

### Captcha handling (three layers)

1. **Local ONNX** — `modules/captcha_solver.py` runs `models/best.onnx` over "letterword" image
   captchas and replies with the predicted letters.
2. **Paid hCaptcha services** — `modules/web_solver.py` performs the Discord OAuth → `owobot.com/captcha`
   → `/api/captcha/verify` flow, delegating the solve to `modules/services/{yescaptcha,nopecha,
   anticaptcha,captchaly}.py` (each exposes `get_balance` / `solve_hcaptcha`).
3. **Manual** — `WebSolver.enqueue_manual_solve` puts the account on a class-level queue serialized by
   `_manual_lock` (one browser/one solve at a time across all accounts) and waits on a future;
   `mark_verification_done(bot_id)` resolves it. `dashboard/app.py` mirrors this in `_pending_captchas`
   via `register_captcha_challenge` / `clear_captcha_challenge` so the UI can render a solve panel.

`cogs/security.py` is the detector (ban keywords, `(n/m)` warning counters, image captchas, captcha
links) and decides which layer to use; it also fires desktop/Termux notifications, a beep, and a Discord
webhook. Note `dashboard/app.py` monkeypatches `socket.getaddrinfo` to pin `owobot.com` to a hardcoded
IP.

### Components V2

OwO now sends "components v2" messages that `discord.py-self` does not model (`message.content` is
empty). `cogs/quest.py` and `cogs/others.py` therefore listen on `on_socket_raw_receive` and walk the raw
payload with `component_v2_neura/parser.py` (`parse_v2_message` → flat `V2Component` list). Clicking those
buttons goes through `component_v2_neura/interactions.py`, which hand-builds a
`POST /api/v9/interactions` with spoofed `X-Super-Properties` (it scrapes Discord's current build number).
Quest and zoo/team/level parsing each have both a V2 path and a legacy embed path.

`buttons(components)` **excludes disabled buttons**, and OwO only enables a quest claim button while the
reward is actually waiting — so quest claiming is driven off "is there an enabled claim button" rather
than re-deriving completion from the `N/M` progress text (`Quest._claim_targets`). Claims are deduped per
`(message_id, custom_id)` because OwO edits the card after each claim and MESSAGE_UPDATE re-enters the
parser; a rejected click un-deduped so the next card retries. Gate: `commands.quest.auto_claim`.

### Accounts and proxies

`config/accounts.json` is `{"accounts": [{name, token, channels, enabled, proxy_id, status, ...}]}`.
`utils/proxy_manager.py` is the single reader/writer for it *and* the proxy pool (`config/proxies.json`,
parse/bulk-import/test/auto-assign). `bot.flag_account(status, reason)` writes back
`invalid_token` / `needs_verification` / `cannot_send` so the dashboard can group broken accounts and
`/api/accounts/export?only=problem` can dump them. SOCKS proxies get an `aiohttp_socks.ProxyConnector`.

### Owner commands

`cogs/owner.py` lets the operator drive every farm account from their own Discord account. Config
section `owner` (`enabled`, `user_id`, `trigger` — default `farmers`). `farmers pay` / `send` /
`showbal` are special-cased; anything else is forwarded verbatim as an OwO command. An account name or
user id token after the trigger (`farmers acc2 bal`) narrows it to one account.

### Cross-account coop

Every `NeuraBot` shares **one** asyncio loop (`neura.py` binds it, `supervisor.start_account` creates each
runner as a task on it), so `await peer.neura_enqueue(...)` across instances is safe — no
`run_coroutine_threadsafe`. `neura_engines/coop.py` is the single place that decides whether a sibling may
be leaned on: `peers(bot)` returns live accounts that are ready, unpaused, past warmup, not sitting on an
unsolved captcha (`throttle_until == inf`) **and** sharing a channel with the asker (`shared_channel` —
OwO only credits a social interaction it can see both sides of). `is_initiator(bot, peer)` compares user
ids so exactly one side of a two-sided action starts it, and `may_ask`/`note_ask` hold a
process-wide `(giver, receiver, action)` cooldown table.

`cogs/coop.py` schedules the periodic friendly battle (`coop_offer`); `neura_engines/quest_engine.py`
routes social quests (pray/curse/cookie/emote/battle-with-a-friend) through `coop.ask_peer`.
`cogs/response_handler.py` already auto-accepts any duel it is mentioned in and stamps
`bot.last_duel_accept`, which `Coop.arm_accept_fallback` checks before sending a backup `owo ab`.
Config section `coop` (`enabled`, `quests.enabled`, `battle.{enabled,interval_min,min_gap_s,arbitrate}`,
`fallback_targets`). `fallback_targets` is a *list* of user ids — it is in `isListField` in
`dashboard/static/js/config.js` so the UI writes an array, not a comma string.

### Zoo team watcher

`cogs/others.py` owns both the OwO level sync and the battle team. `parse_zoo` reads the zoo card (v2 or
legacy), ranks every owned animal, and `_apply_team_upgrade` swaps in anything rarer — with hysteresis so
a same-tier tie does not churn the team every scan. `_watch_hunt` is the watcher: a hunt result is the
moment the zoo changes, so a catch rarer than the weakest team slot triggers `request_team_check`
immediately instead of waiting for the `team_scan` timer. Config: `commands.team.{enabled,slots,watch_zoo,
min_action_gap_s}`.

When OwO answers `owo level` with a rendered image card, `_note_level_unreadable()` sets
`stats['level_source'] = 'image'` and leaves level/xp blank; the dashboard renders "image card ·
unreadable" rather than a stale number. Do not reintroduce a guess here.

### Dashboard frontend

`dashboard/templates/index.html` is a single page of `.view` divs toggled by `window.nav()`. Plain
global-function JS, **load order matters**: `core.js` declares the shared globals (`currentConfig`,
`currentAccountId`, chart handles), feature files follow, `init.js` is last and wires
`DOMContentLoaded` plus the polling intervals (1 s stats, 2 s captcha, 5 s accounts). CSS is split
`base/layout/components/responsive` + `pages/*.css`. Every css *and* js tag carries
`?v={{ asset_v }}`, injected by the `inject_asset_version` context processor in `dashboard/app.py` from
the newest mtime under `static/` — so an edited file busts the cache on the next reload. No bundler —
edit and reload.

The config view is generated from the config object itself (`buildConfigCategories` in `config.js`), so a
new settings key appears without frontend work; only its hint text (`CONFIG_CATEGORY_HINTS` /
`CONFIG_CMD_HINTS` in `core.js`) and any list/select special-casing need adding.

## Conventions

- Every source file starts with the GPL header block and an `Author: Routo` docstring; copy that when
  adding files.
- New cogs are auto-discovered: `NeuraBot._load_cogs` loads every `cogs/*.py`. Provide
  `async def setup(bot)`; add `register_actions()` if the cog schedules commands (it is re-run on every
  ready and on relevant config changes, so it must be idempotent).
- Log through `bot.log(TYPE, message)`, not `print`. Types in use: `SYS`, `CMD`, `INFO`, `SUCCESS`,
  `COOLDOWN`, `STEALTH`, `GAMBLING`, `SECURITY`, `ALARM`, `WARN`, `ERROR`, `DEBUG`; colors come from
  `config/logmisc.json`.
- `NeuraBot.check_version` compares the hardcoded `CURRENT_VERSION` in `core/bot.py` against a remote
  `version.json` and calls `sys.exit(0)` on mismatch — bots refuse to start when it disagrees.
- Secrets live in `config/auth.json`, `config/accounts.json` and the generated
  `config/settings_<user_id>.json`; none of these are gitignored, so don't commit a populated copy.
