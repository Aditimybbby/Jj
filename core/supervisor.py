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
    return getattr(bot, 'owner_id', spaces.ADMIN_SPACE)


def running_names(owner=None):
    return [
        account_name(bot) for bot in state.bot_instances
        if account_name(bot) and (owner is None or bot_owner(bot) == owner)
    ]


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
        owner_id=owner,
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
            await session.close()


async def stop_account(owner, name):
    bot = find_bot(owner, name)
    if not bot:
        return False, f"{name} is not running"

    bot.active = False
    bot.paused = True
    try:
        await bot.close()
    except Exception as e:
        bot.log("ERROR", f"Error while closing session: {e}")
    if bot in state.bot_instances:
        state.bot_instances.remove(bot)
    return True, f"{name} stopped"


async def start_all(accounts, owner=spaces.ADMIN_SPACE):
    results = []
    for i, account in enumerate(accounts):
        if i > 0:
            await asyncio.sleep(random.uniform(2.5, 4.5))
        try:
            results.append(await start_account(account, owner))
        except Exception as e:
            results.append((False, f"{account.get('name', 'unnamed')}: {e}"))
    return results


async def stop_all(owner=None):
    return [
        await stop_account(bot_owner(bot), account_name(bot))
        for bot in list(state.bot_instances)
        if account_name(bot) and (owner is None or bot_owner(bot) == owner)
    ]
