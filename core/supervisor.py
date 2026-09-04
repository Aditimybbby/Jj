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
import time

import core.state as state
from core import spaces
from core.bot import NeuraBot
from utils import proxy_manager

_loop = None
_heartbeat_task = None


def bind_loop(loop):
    """Adopt the loop every bot shares, and start its heartbeat.

    The heartbeat is what lets the dashboard tell "the loop is busy" from "the
    loop is fine, this account is slow". Flask runs on other threads and every
    call into the loop has to be scheduled onto it, so without a liveness signal
    a blocked loop looked exactly like a hung web server: requests piled up on
    the waitress thread pool until nothing was left to serve the page itself.
    """
    global _loop, _heartbeat_task
    _loop = loop
    state.loop_heartbeat = time.time()
    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = loop.create_task(_heartbeat())


async def _heartbeat():
    while True:
        state.loop_heartbeat = time.time()
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return


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


async def start_all(accounts, owner=spaces.ADMIN_SPACE):
    """Bring a whole space up.

    There is deliberately no sleep between accounts any more. The old
    2.5-4.5s (later 1-2s) stagger existed to keep Discord from seeing a burst of
    logins, but it only ever slowed down *this* path - the reconnect loop in
    run_bot never passed through here, so one network blip still threw the whole
    farm at the gateway at once. Metering moved into `state.login_slot()`, which
    every login goes through, so this function's only job is to create the tasks.

    That matters for more than speed: 200 accounts x 1-2s is 200-400s, longer
    than waitress' channel_timeout, so the dashboard's Start All returned an
    error to a browser whose accounts were in fact still coming up - the operator
    would press it again and two passes would interleave.
    """
    results = []
    started = 0
    # read the proxy pool once instead of once per account (each read is a full
    # json.load of proxies.json, on the loop, before the first login)
    proxies = proxy_manager.load_proxies(owner)
    for i, account in enumerate(accounts):
        try:
            ok, message = await start_account(account, owner, proxies=proxies)
            results.append((ok, message))
            if ok:
                started += 1
        except Exception as e:
            results.append((False, f"{account.get('name', 'unnamed')}: {e}"))
        # NeuraBot.__init__ arms cogs and builds a session; 200 of those back to
        # back would hold the loop long enough for the dashboard's own calls into
        # it to time out. Yield often enough that it stays answerable.
        if i % 20 == 19:
            await asyncio.sleep(0)
    return {'results': results, 'started': started, 'total': len(accounts)}


# Teardown is bounded rather than unbounded: each stop_account may wait up to 10s
# on a runner task and then close a gateway connection, so serial teardown of 200
# accounts could not finish inside any sane HTTP timeout - which is what left the
# farm half-stopped with autostart already cleared for everything.
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
