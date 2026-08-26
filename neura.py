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
import socket
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
    # os.execv replaces this process, so a setup that "succeeds" without actually
    # fixing the imports used to re-exec forever. On a host with a console you see
    # the banner scroll past again and again; on Railway you see a service that
    # stays "running" and never serves a single request, because the dashboard is
    # never reached. Two tries, then say what is missing and fail properly.
    _attempts = 0
    try:
        _attempts = int(os.environ.get("LAZYFARMERS_SETUP_ATTEMPTS", "0") or 0)
    except ValueError:
        _attempts = 0
    if _attempts >= 2:
        console.print("[red]Dependencies are still missing after two setup attempts.[/red]")
        console.print("[red]Run 'python neura_setup.py' and read data/setup.log - re-running "
                      "the installer again would only loop.[/red]")
        sys.exit(1)
    console.print("[yellow]Environment not healthy – running setup...[/yellow]")
    if not engine.run_full_setup(force_bootstrap=True):
        console.print("[red]Setup failed. Please run 'python neura_setup.py' manually.[/red]")
        sys.exit(1)
    console.print("[green]Setup complete. Restarting...[/green]")
    os.environ["LAZYFARMERS_SETUP_ATTEMPTS"] = str(_attempts + 1)
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

DEFAULT_PORT = 8000


def _dashboard_port():
    """(port, came_from_env). Railway injects PORT; everything else defaults."""
    raw = (os.environ.get('PORT') or '').strip()
    if not raw:
        return DEFAULT_PORT, False
    try:
        return int(raw), True
    except ValueError:
        console.print(f"[bold red]PORT={raw!r} is not a number - using {DEFAULT_PORT}.[/bold red]")
        return DEFAULT_PORT, False


def _bind_dashboard(port):
    """Claim the listening socket up front, in the main thread.

    A platform edge (Railway's, a reverse proxy, anything) decides a container is
    up by connecting to a port. Binding here rather than inside the server thread
    means "nothing is listening" is a startup failure with a reason in the log,
    not an exception swallowed by a daemon thread.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == 'posix':
        # POSIX only, deliberately. On Windows SO_REUSEADDR lets a second process
        # bind a port that is already being listened on, so the "nothing else has
        # this port" guarantee above would quietly stop holding.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('0.0.0.0', port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def _serve_dashboard(sock, port):
    """Serve Flask on an already-bound socket, on the best server available."""
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        waitress_serve = None

    if waitress_serve is not None:
        # threads: the dashboard polls stats every second and several routes block
        # on the bot loop for up to a minute, so waitress' default of 4 would let
        # one slow Start button freeze every other tab.
        console.print("[green]Serving the dashboard with waitress.[/green]")
        waitress_serve(flask_app, sockets=[sock], threads=16, channel_timeout=180)
        return

    from werkzeug.serving import make_server
    console.print("[yellow]waitress is not installed - falling back to werkzeug's "
                  "development server (fine locally, slow under load).[/yellow]")
    make_server('0.0.0.0', port, flask_app, threaded=True, fd=sock.fileno()).serve_forever()


def run_dashboard(sock, port):
    try:
        _serve_dashboard(sock, port)
        reason = 'the web server returned on its own'
    except BaseException as e:
        reason = f'{type(e).__name__}: {e}'
        console.print_exception(show_locals=False)
    _dashboard_died(reason)


def _dashboard_died(reason):
    """The web server is gone, so take the process with it.

    This is what made "it says running but the site does not load" possible: the
    dashboard runs in a daemon thread, main() then parks in `await
    asyncio.sleep(60)` forever, and nothing anywhere checked whether the server
    was still alive. A crash inside flask_app.run() printed nothing (the
    "listening" line had already been printed), left the process happily idling
    with no listener, and because the exit code stayed 0 the platform's
    restart-on-failure policy never fired.
    """
    console.print(f"[bold red]The dashboard web server stopped: {reason}[/bold red]")
    console.print("[bold red]Nothing is listening any more - exiting so the host restarts us.[/bold red]")
    _shutdown()
    # a daemon thread cannot end the process by raising, and the asyncio loop is
    # asleep in main(); this is the only exit that actually happens.
    os._exit(1)


_dashboard_thread = None


def start_dashboard():
    global _dashboard_thread
    if _dashboard_thread is not None:
        return
    port, from_env = _dashboard_port()
    try:
        sock = _bind_dashboard(port)
    except OSError as e:
        console.print(f"[bold red]Cannot listen on 0.0.0.0:{port}: {e}[/bold red]")
        if from_env:
            console.print("[bold red]$PORT asked for that port, so nothing could ever reach "
                          "the dashboard on it.[/bold red]")
        else:
            console.print(f"[bold red]Stop whatever is already using {port}, or set PORT "
                          f"to a free port.[/bold red]")
        sys.exit(1)
    console.print(f"[bold green]Dashboard listening on http://0.0.0.0:{port}[/bold green]")
    if not from_env:
        console.print(f"[yellow]No PORT was set, so the dashboard is on {port}. If this is a hosted "
                      f"deploy, the public domain's target port must be {port} - a mismatch is "
                      f"answered with 502 while the service still reads as running.[/yellow]")
    _dashboard_thread = threading.Thread(target=run_dashboard, args=(sock, port),
                                        name='dashboard', daemon=True)
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
_shutdown_done = threading.Event()


def _shutdown():
    """Close history sessions and flush stats. Safe to call from any thread, once."""
    if _shutdown_done.is_set():
        return
    _shutdown_done.set()
    try:
        import utils.history_tracker as ht
        for owner in list(_started_spaces):
            ht.end_session(owner=owner)
        # force: the debounced write would otherwise be scheduled on a timer that
        # never gets to run, losing everything since the last flush
        state.save_account_stats(force=True)
        console.print("\n[bold yellow][!] Systems shut down. History saved.[/bold yellow]")
    except Exception as e:
        console.print(f"[red]Shutdown bookkeeping failed: {e}[/red]")

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
            # opens the space's history db (and migrates an old schema) off the loop
            await asyncio.to_thread(ht.start_session, owner=owner)
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
    exit_code = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        # the code was being swallowed here, so a fatal startup problem still exited
        # 0 and a restart-on-failure policy had nothing to react to
        code = e.code
        exit_code = code if isinstance(code, int) else (0 if code is None else 1)
    finally:
        _shutdown()
    sys.exit(exit_code)