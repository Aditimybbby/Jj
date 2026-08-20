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
"""

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

from neura_engines.setup_engine import NeuraSetupEngine
from core.bot import NeuraBot
from core import supervisor
from dashboard.app import app as flask_app
import core.state as state
from utils import proxy_manager

console = Console()
engine = NeuraSetupEngine()

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

def load_enabled_accounts():
    try:
        with open(os.path.join(state.CONFIG_DIR, 'accounts.json'), 'r') as f:
            acc_data = json.load(f)
        return [a for a in acc_data.get('accounts', []) if a.get('enabled', True)]
    except (OSError, ValueError):
        return []

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
        console.print(f"[cyan]Accounts File:[/cyan] {os.path.join(state.CONFIG_DIR, 'accounts.json')}\n")
        if not HEADLESS:
            console.print("\n[bold cyan]1.[/bold cyan] Start Lazy Farmers")
            console.print("[bold cyan]2.[/bold cyan] Manage Accounts")
            console.print("[bold cyan]3.[/bold cyan] Exit")
            from rich.prompt import Prompt
            try:
                choice = Prompt.ask("\nSelect option", choices=["1", "2", "3"], default="1")
            except EOFError:
                console.print("\n[yellow]No console input - starting the enabled accounts.[/yellow]")
                choice = "1"
            if choice == "2":
                import neura_setup
                await neura_setup.account_manager()
                continue
            elif choice == "3":
                console.print("\n[yellow]Shutting down. See you next time![/yellow]")
                sys.exit(0)
        accounts = load_enabled_accounts()
        if not accounts and not HEADLESS:
            console.print("[bold red]No active accounts? Add some in the Account Manager (Option 2).[/bold red]")
            time.sleep(2)
            continue
        import utils.history_tracker as ht
        ht.start_session()
        if accounts:
            console.print(f"[bold yellow]Initializing {len(accounts)} accounts...[/bold yellow]")
            for ok, message in await supervisor.start_all(accounts):
                console.print(f"[green]{message}[/green]" if ok else f"[bold red]{message}[/bold red]")
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
            ht.end_session()
            state.save_account_stats()
            console.print("\n[bold yellow][!] Systems shut down. History saved.[/bold yellow]")
        except Exception:
            pass