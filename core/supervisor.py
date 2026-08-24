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
"""


import asyncio
import random

import core.state as state
from core import spaces
from core.bot import NeuraBot
from utils import proxy_manager

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


async def start_account(account, owner=spaces.ADMIN_SPACE):
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

    proxy_url, proxy_auth, proxy_label = proxy_manager.resolve_account_proxy(owner, account)
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
        session = getattr(bot, 'session', None)
        if session is not None and not session.closed:
            try:
                await session.close()
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

    # Belt and suspenders: cancel any stray background workers the bot spawned
    # (_track_active_time, _process_pending_commands, neura workers) so they do
    # not keep firing after the account is gone.
    for attr in ('neura_scheduler_task',):
        task = getattr(bot, attr, None)
        if task is not None and not task.done():
            task.cancel()
    return True, f"{name} stopped"


async def start_all(accounts, owner=spaces.ADMIN_SPACE):
    results = []
    started = 0
    for i, account in enumerate(accounts):
        # A short stagger keeps Discord from seeing a burst of logins, but the old
        # 2.5-4.5s gap meant a 10-account farm took ~40s to even begin - and the
        # dashboard, re-fetching immediately, listed them all as "stopped".
        if i > 0:
            await asyncio.sleep(random.uniform(1.0, 2.0))
        try:
            ok, message = await start_account(account, owner)
            results.append((ok, message))
            if ok:
                started += 1
        except Exception as e:
            results.append((False, f"{account.get('name', 'unnamed')}: {e}"))
    return {'results': results, 'started': started, 'total': len(accounts)}


async def stop_all(owner=None):
    targets = [
        bot for bot in list(state.bot_instances)
        if account_name(bot) and (owner is None or bot_owner(bot) == owner)
    ]
    results = []
    for bot in targets:
        try:
            results.append(await stop_account(bot_owner(bot), account_name(bot)))
        except Exception as e:
            results.append((False, f"{account_name(bot)}: {e}"))
    return results
