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
Author: Routo
LazyFarmers - https://github.com/routo-loop/neura-self

Runs the bot instances so the dashboard can start and stop accounts while the
process keeps running.
"""


import asyncio
import random

import core.state as state
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


def running_names():
    return [account_name(bot) for bot in state.bot_instances if account_name(bot)]


def find_bot(name):
    for bot in state.bot_instances:
        if account_name(bot) == name:
            return bot
    return None


def is_placeholder(value):
    text = str(value or '')
    return not text or "YOUR_TOKEN_HERE" in text or "YOUR_CHANNEL_ID_HERE" in text or "PLACEHOLDER" in text


async def start_account(account):
    name = account.get('name') or 'unnamed'
    if find_bot(name):
        return False, f"{name} is already running"

    token = account.get('token')
    if is_placeholder(token):
        return False, f"{name} has no usable token"

    channels = [c for c in (account.get('channels') or []) if not is_placeholder(c)]
    if not channels:
        return False, f"{name} has no channel id"

    proxy_url, proxy_auth, proxy_label = proxy_manager.resolve_account_proxy(account)
    bot = NeuraBot(
        token=token,
        channels=channels,
        proxy_url=proxy_url,
        proxy_auth=proxy_auth,
        proxy_label=proxy_label,
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


async def stop_account(name):
    bot = find_bot(name)
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


async def start_all(accounts):
    results = []
    for i, account in enumerate(accounts):
        if i > 0:
            await asyncio.sleep(random.uniform(2.5, 4.5))
        try:
            results.append(await start_account(account))
        except Exception as e:
            results.append((False, f"{account.get('name', 'unnamed')}: {e}"))
    return results


async def stop_all():
    return [await stop_account(name) for name in running_names()]
