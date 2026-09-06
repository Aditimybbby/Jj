# This file is part of LazyFarmers.
# Copyright (c) 2025-Present Routo
#
# LazyFarmers is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with LazyFarmers. If not, see <https://www.gnu.org/licenses/>.


"""
Runs the bot instances so the dashboard can start and stop accounts while the
process keeps running.

Accounts are addressed by (owner, name) - two dashboard users may both call an
account "acc1", so a bare name is never enough to find the right bot.

Nothing in here starts an account on its own. Every bot that exists was asked
for by an explicit click on the dashboard's Start button; a restart, a redeploy
or a crash brings the process back with an empty farm.
"""


import asyncio

import core.state as state
import utils.history_tracker as ht
from core import spaces
from core.bot import NeuraBot
from utils import proxy_manager

# Spaces whose history db has been opened this run. Boot used to do this for
# every space up front; now the first Start in a space opens it.
started_spaces = set()

_loop = None


def bind_loop(loop):
    global _loop
    _loop = loop


def get_loop():
    return _loop


def account_name(bot):
    return getattr(bot, 'account_name', None)


def bot_owner(bot):
    # the space id lives on space_owner, never on owner_id - commands.Bot owns that name
    return getattr(bot, 'space_owner', spaces.ADMIN_SPACE)


def running_names(owner=None):
    return [
        account_name(bot) for bot in state.bot_instances
        if account_name(bot) and (owner is None or bot_owner(bot) == owner)
    ]


def running_states(owner=None):
    """name -> True once the account reached READY, False while it is still connecting.

    "has an instance" and "is logged in" are different things: a bot with a dead
    proxy sits in the reconnect loop forever, so the UI needs to say CONNECTING
    rather than claim it is farming.
    """
    return {
        account_name(bot): bool(getattr(bot, 'is_ready', False))
        for bot in state.bot_instances
        if account_name(bot) and (owner is None or bot_owner(bot) == owner)
    }


def find_bot(owner, name):
    for bot in state.bot_instances:
        if account_name(bot) == name and bot_owner(bot) == owner:
            return bot
    return None


def is_placeholder(value):
    text = str(value or '')
    return not text or "YOUR_TOKEN_HERE" in text or "YOUR_CHANNEL_ID_HERE" in text or "PLACEHOLDER" in text


def running_duplicate(token, user_id=None, ignore=None):
    """The live bot already using this token or discord id, in *any* space.

    The only guard used to be (owner, name), which answers "is this config row
    running" - not "is this Discord account running". The same token pasted
    under a second name, or added by two dashboard users, sailed straight past
    it and logged in twice: two gateway sessions farming one account, sending
    every command twice. That is the shape of a selfbot ban.

    Token is the pre-login identity (it is all we have before READY); user_id
    catches the case where two config rows hold different tokens for the same
    Discord account.
    """
    token = str(token or '').strip()
    uid = str(user_id or '').strip()
    for bot in state.bot_instances:
        if bot is ignore:
            continue
        if token and str(getattr(bot, 'token', '') or '').strip() == token:
            return bot
        if uid and str(getattr(bot, 'user_id', '') or '').strip() == uid:
            return bot
    return None


def _open_space_history(owner):
    """Open this space's history db the first time it starts something.

    Boot used to do this for every space before starting its accounts. With
    nothing auto-starting, the first Start click is the moment a space becomes
    active, so that is where the db is opened. start_session is idempotent.
    """
    if owner in started_spaces:
        return
    try:
        ht.start_session(owner=owner)
    except Exception:
        return
    started_spaces.add(owner)


async def start_account(account, owner=spaces.ADMIN_SPACE, proxies=None):
    owner = spaces.normalise_owner(owner)
    name = account.get('name') or 'unnamed'
    if find_bot(owner, name):
        return False, f"{name} is already running"

    token = account.get('token')
    if is_placeholder(token):
        return False, f"{name} has no usable token"

    channels = [c for c in (account.get('channels') or []) if not is_placeholder(c)]
    if not channels:
        return False, f"{name} has no channel id"

    # No await between here and the append below, and every caller is on the one
    # shared loop - so a double-clicked Start cannot slip a second instance in
    # between the check and the registration.
    twin = running_duplicate(token, account.get('user_id'))
    if twin:
        twin_name = account_name(twin) or 'another entry'
        if bot_owner(twin) != owner:
            return False, (f"{name} is the same Discord account as one already "
                           f"running in another space")
        return False, f"{name} is the same Discord account as {twin_name}, already running"

    _open_space_history(owner)

    proxy_url, proxy_auth, proxy_label = proxy_manager.resolve_account_proxy(
        owner, account, proxies=proxies)
    bot = NeuraBot(
        token=token,
        channels=channels,
        proxy_url=proxy_url,
        proxy_auth=proxy_auth,
        proxy_label=proxy_label,
        space_owner=owner,
    )
    bot.account_name = name
    state.bot_instances.append(bot)
    bot.runner_task = asyncio.create_task(_run(bot))
    return True, f"{name} is starting"


async def _run(bot):
    """Keep state.bot_instances honest - a bot that gave up is no longer running."""
    try:
        await bot.run_bot()
    finally:
        if bot in state.bot_instances:
            state.bot_instances.remove(bot)
        _release_captcha_hold(bot)
        session = getattr(bot, 'session', None)
        if session is not None and not session.closed:
            try:
                await session.close()
            except Exception:
                pass


def _release_captcha_hold(bot):
    """Withdraw anything this account still holds in the captcha machinery.

    A pending challenge is a claim that this account is waiting on a human right now.
    Once the bot is gone nothing can solve for it, so leaving the claim behind put a
    permanent entry in the dashboard's notification bell whose Solve button could only
    ever answer "that account is not running" - and left the single manual-solve slot
    held against every other account.
    """
    user = getattr(bot, 'user', None)
    bot_id = str(user.id) if user else None
    if not bot_id:
        return
    try:
        from modules.web_solver import WebSolver
        WebSolver.abandon_manual_solve(bot_id)
    except Exception:
        pass
    try:
        from dashboard.app import clear_captcha_challenge
        clear_captcha_challenge(bot_id)
    except Exception:
        pass


async def stop_account(owner, name):
    bot = find_bot(owner, name)
    if not bot:
        return False, f"{name} is not running"

    # Signal every loop in run_bot / workers to bail out before we touch the
    # gateway, otherwise a bot caught mid-reconnect-sleep (up to 300s) would
    # ignore the stop and the dashboard would keep listing it as running.
    bot.active = False
    bot.paused = True

    runner = getattr(bot, 'runner_task', None)

    # Cancel the runner first so a long reconnect backoff can not out-live the
    # close() call. close() alone leaves the task parked in asyncio.sleep().
    if runner is not None and not runner.done():
        runner.cancel()
        try:
            # shield so a wait_for timeout does not eat the cancel we just sent;
            # if it is still winding down after 10s, close() below finishes it.
            await asyncio.wait_for(asyncio.shield(runner), timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            pass

    try:
        await bot.close()
    except Exception as e:
        bot.log("ERROR", f"Error while closing session: {e}")

    # The runner's finally block also removes the bot; guard the double remove.
    if bot in state.bot_instances:
        state.bot_instances.remove(bot)
    _release_captcha_hold(bot)

    # Belt and suspenders: cancel any stray background workers the bot spawned
    # (_track_active_time, _process_pending_commands, neura_queue_worker,
    # neura_scheduler_worker) so they do not keep firing after the account is
    # gone. They loop on `while self.active` and would exit on their own, but
    # cancelling closes the race where a worker wakes between active=False and
    # close() and tries to send on a shutting gateway.
    for task in (getattr(bot, 'worker_tasks', None) or []):
        if task is not None and not task.done():
            task.cancel()
    return True, f"{name} stopped"


# There is deliberately no start_all. A mass start is how a redeploy put ~16
# accounts on the gateway inside eight seconds, buried the host's log viewer and
# took the process out on thread exhaustion; it is also how a double-pressed
# button interleaved two passes over the same farm. Accounts come up one click at
# a time, through start_account.
#
# stop_all survives because teardown has callers that are not the operator: the
# process is shutting down, or a dashboard user was deleted and their accounts
# must not outlive their accounts.json. It is not reachable from the UI.
#
# Teardown is bounded rather than unbounded: each stop_account may wait up to 10s
# on a runner task and then close a gateway connection, so serial teardown of 200
# accounts could not finish inside any sane HTTP timeout.
STOP_CONCURRENCY = 16


async def stop_all(owner=None):
    targets = [
        bot for bot in list(state.bot_instances)
        if account_name(bot) and (owner is None or bot_owner(bot) == owner)
    ]

    async def _stop(bot):
        try:
            return await stop_account(bot_owner(bot), account_name(bot))
        except Exception as e:
            return (False, f"{account_name(bot)}: {e}")

    results = []
    for i in range(0, len(targets), STOP_CONCURRENCY):
        batch = targets[i:i + STOP_CONCURRENCY]
        results.extend(await asyncio.gather(*(_stop(bot) for bot in batch)))
    return results
