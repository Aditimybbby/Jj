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


import sys
import os

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import subprocess
import asyncio
import random
import json
import threading
import time
from rich.console import Console
from rich.align import Align

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lazy_engines.setup_engine import LazySetupEngine
from core.bot import NeuraBot
from core import spaces, supervisor
from dashboard.app import app as flask_app
import core.state as state
from utils import proxy_manager

console = Console()
engine = LazySetupEngine()

# no interactive menu on hosts without a usable console. Railway attaches a tty that
# nobody can type into, so the menu would block forever and the dashboard would never start.
HEADLESS = (
    os.environ.get("LAZYFARMERS_HEADLESS", "").lower() in ("1", "true", "yes")
    or any(key.startswith("RAILWAY_") for key in os.environ)
    or not sys.stdin.isatty()
)

if not engine.environment_healthy():
    console.print("[yellow]Environment not healthy – running setup...[/yellow]")
    if not engine.run_full_setup(force_bootstrap=True):
        console.print("[red]Setup failed. Please run 'python neura_setup.py' manually.[/red]")
        sys.exit(1)
    console.print("[green]Setup complete. Restarting...[/green]")
    os.execv(sys.executable, [sys.executable] + sys.argv)

def show_banner():
    from neuraself_ascii import neura_ascii
    neura_ascii.show_banner('main', animate=not HEADLESS)

def detect_platform():
    if "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", ""):
        platform = "Mobile (Termux)"
        is_termux = True
    elif sys.platform.startswith("linux"):
        platform = "Linux (Server/Desktop)"
        is_termux = False
    elif sys.platform == "darwin":
        platform = "MacOS"
        is_termux = False
    elif os.name == "nt":
        platform = "PC (Windows)"
        is_termux = False
    else:
        platform = f"Unknown ({sys.platform})"
        is_termux = False
    console.print(f"[bold green]Detected Platform: {platform}[/bold green]")
    return is_termux

def run_dashboard():
    port = int(os.environ.get('PORT', 8000))
    console.print(f"[bold green]Dashboard listening on 0.0.0.0:{port}[/bold green]")
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

_dashboard_thread = None

def start_dashboard():
    global _dashboard_thread
    if _dashboard_thread is None:
        _dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
        _dashboard_thread.start()

def load_enabled_accounts(owner):
    """Accounts this space wants running right now.

    `enabled` is "this account is part of my farm"; `autostart` is "and it should
    be up". The dashboard's Stop button clears autostart, so an account the
    operator stopped stays stopped through a restart or a redeploy instead of
    quietly coming back and farming behind their back.
    """
    return [
        a for a in proxy_manager.load_accounts(owner)
        if a.get('enabled', True) and proxy_manager.wants_autostart(a)
    ]


_started_spaces = set()

def spaces_with_accounts():
    """(owner, accounts) for every space that has something to start.

    The admin space comes first so the operator's own farm is up before any
    dashboard user's, and it is always listed even when empty - the menu below
    reports on it.
    """
    out = []
    for owner in spaces.list_owners():
        accounts = load_enabled_accounts(owner)
        if accounts or owner == spaces.ADMIN_SPACE:
            out.append((owner, accounts))
    return out


def configured_account_count():
    """How many accounts exist across every space, autostart or not."""
    return sum(len(proxy_manager.load_accounts(owner)) for owner in spaces.list_owners())


def _menu_choice():
    """The blocking console prompt, to be run off the event loop.

    Both this and the account manager below used to run directly on the asyncio
    loop. The dashboard is already serving by then, so a prompt sitting on the
    loop parked every request that needs it - start, stop, verify, manual command
    all queue a coroutine onto `supervisor`'s loop and wait for a result that can
    never arrive. Option 2 held the loop for the whole terminal session.
    """
    from rich.prompt import Prompt
    try:
        return Prompt.ask("\nSelect option", choices=["1", "2", "3"], default="1")
    except EOFError:
        console.print("\n[yellow]No console input - starting the enabled accounts.[/yellow]")
        return "1"


def _run_account_manager():
    """neura_setup.account_manager() on its own loop, in a worker thread.

    It is declared `async def`, but every line inside it is a blocking Prompt or
    input(); the one await is a self-contained token verification, so giving it a
    private loop costs nothing and leaves the main one free to keep serving bots.
    """
    import neura_setup
    asyncio.run(neura_setup.account_manager())


async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    supervisor.bind_loop(asyncio.get_running_loop())
    # before the banner and the menu, so a blocked console can never hide the dashboard
    start_dashboard()
    while True:
        show_banner()
        is_termux = detect_platform()
        state.load_account_stats()
        console.print(f"[cyan]Config Directory:[/cyan] {state.CONFIG_DIR}")
        console.print(f"[cyan]Accounts File:[/cyan] {spaces.accounts_path(spaces.ADMIN_SPACE)}\n")
        if not HEADLESS:
            console.print("\n[bold cyan]1.[/bold cyan] Start Lazy Farmers")
            console.print("[bold cyan]2.[/bold cyan] Manage Accounts")
            console.print("[bold cyan]3.[/bold cyan] Exit")
            choice = await asyncio.to_thread(_menu_choice)
            if choice == "2":
                await asyncio.to_thread(_run_account_manager)
                continue
            elif choice == "3":
                console.print("\n[yellow]Shutting down. See you next time![/yellow]")
                sys.exit(0)
        # every dashboard user has their own space, so boot them all - not just the
        # operator's (see core/spaces.py)
        pending = spaces_with_accounts()
        total = sum(len(accounts) for _owner, accounts in pending)
        # Only re-prompt when there is genuinely nothing configured. Accounts that
        # exist but were stopped from the dashboard must not send us back around
        # the menu loop - that would keep the process from ever reaching the idle
        # state where the dashboard can start them again.
        if not total and not configured_account_count() and not HEADLESS:
            console.print("[bold red]No active accounts? Add some in the Account Manager (Option 2).[/bold red]")
            time.sleep(2)
            continue
        import utils.history_tracker as ht
        for owner, accounts in pending:
            if not accounts:
                continue
            ht.start_session(owner=owner)
            _started_spaces.add(owner)
            label = "operator" if owner == spaces.ADMIN_SPACE else owner
            console.print(f"[bold yellow]Initializing {len(accounts)} accounts for {label}...[/bold yellow]")
            start_result = await supervisor.start_all(accounts, owner)
            # start_all now returns {'results': [(ok,msg),...], 'started': n, 'total': m}
            for ok, message in start_result.get('results', start_result if isinstance(start_result, list) else []):
                console.print(f"[green]{message}[/green]" if ok else f"[bold red]{message}[/bold red]")
        if not total:
            configured = configured_account_count()
            if configured:
                console.print(f"[bold yellow]{configured} account(s) configured, none set to autostart - "
                              f"start them from the dashboard Accounts page.[/bold yellow]")
            else:
                console.print("[bold yellow]No enabled accounts yet - add them on the dashboard Accounts page.[/bold yellow]")
        console.print("[bold green]Dashboard is in control. Start, stop and verify accounts from the Accounts page.[/bold green]")
        while True:
            await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            import utils.history_tracker as ht
            for owner in _started_spaces:
                ht.end_session(owner=owner)
            state.save_account_stats()
            console.print("\n[bold yellow][!] Systems shut down. History saved.[/bold yellow]")
        except Exception:
            pass