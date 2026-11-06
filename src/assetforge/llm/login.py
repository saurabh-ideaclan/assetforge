"""Sign in to a subscription CLI from the app: start the tool's own login, show its link, take the code the user
pastes back, and confirm with the tool's status check.

Each login runs the official command in a pseudo-terminal (the tools only prompt for a code on a TTY), very wide
so links never wrap, in an empty temp folder (in a folder with its own settings Gemini asks whether to trust it
and restarts, which starts a new sign-in and makes the pasted code invalid). Success is decided by the
provider's `signed_in()` check, not by parsing the tool's screen, so the session ends as soon as it holds.

    claude: claude auth login --claudeai      link → approve → paste the code shown
    gemini: gemini        (NO_BROWSER=true)   link → approve → paste the code shown ("Login with Google")
    codex:  codex login                       link → approve; the browser returns to codex itself (no code)

AssetForge never sees a password; the code goes straight to the vendor's tool, which stores the login.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assetforge.config import Settings
from assetforge.errors import InvalidInputError, NotFoundError
from assetforge.llm import get_provider, runner

TIMEOUT_SECONDS = 600
GEMINI_CREDS = Path("~/.gemini/oauth_creds.json")
URL_RE = re.compile(r"https://[^\s\x1b\"'<>│]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-Za-z]|\r")
URL_HOSTS = {
    "claude-cli": ("claude.com/cai/oauth", "claude.ai/oauth"),
    "gemini-cli": ("accounts.google.com/o/oauth2",),
    "codex-cli": ("auth.openai.com",),
}
NEEDS_CODE = {"claude-cli": True, "gemini-cli": True, "codex-cli": False}


def command(provider: str) -> list[str]:
    exe = runner.which({"claude-cli": "claude", "gemini-cli": "gemini", "codex-cli": "codex"}[provider])
    if not exe:
        raise NotFoundError(f"{provider} is not installed", hint="See `assetforge llm models` for install commands")
    return {"claude-cli": [exe, "auth", "login", "--claudeai"], "gemini-cli": [exe],
            "codex-cli": [exe, "login"]}[provider]  # fmt: skip


def prefer_google_login() -> None:
    """Gemini signs in with whatever method settings.json selects; make it 'Login with Google'."""
    path = Path("~/.gemini/settings.json").expanduser()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        data = {}
    auth = data.setdefault("security", {}).setdefault("auth", {})
    if auth.get("selectedType") != "oauth-personal":
        auth["selectedType"] = "oauth-personal"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")


@dataclass
class LoginSession:
    provider: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = "starting"  # starting → waiting → verifying → done | failed | cancelled
    url: str | None = None
    needs_code: bool = True
    account: str | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    output: str = ""
    relogin: bool = False  # already signed in when this started ("Sign in again")
    _proc: subprocess.Popen[bytes] | None = None
    _master: int | None = None
    _tmp: str | None = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "provider": self.provider, "state": self.state, "url": self.url,
                "needs_code": self.needs_code, "account": self.account, "error": self.error,
                "elapsed": round(time.time() - self.started), "timeout": TIMEOUT_SECONDS,
                "output_tail": self.output[-600:] if self.state == "failed" else None}  # fmt: skip


_sessions: dict[str, LoginSession] = {}
_lock = threading.Lock()


def start(provider: str, settings: Settings) -> LoginSession:
    if provider not in URL_HOSTS:
        raise InvalidInputError(f"Sign-in is available for {', '.join(URL_HOSTS)}")
    with _lock:  # one live sign-in per provider: a new one replaces the old
        for s in list(_sessions.values()):
            if s.provider == provider and s.state in ("starting", "waiting", "verifying"):
                _stop(s, "cancelled")
    session = LoginSession(provider=provider, needs_code=NEEDS_CODE[provider])
    argv = command(provider)
    session.relogin = bool(get_provider(provider, settings).signed_in()[0])
    if provider == "gemini-cli":
        prefer_google_login()
        if session.relogin:  # gemini reuses its saved login without asking: set it aside (restored on failure)
            creds = GEMINI_CREDS.expanduser()
            if creds.exists():
                creds.replace(creds.with_name(creds.name + ".assetforge-backup"))
            session.relogin = False  # with no saved login, gemini asks again and success is the new file
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 4000, 0, 0))  # wide: links never wrap
    session._tmp = tempfile.mkdtemp(prefix="assetforge-login-")
    env = runner.clean_env() | {"NO_BROWSER": "true", "TERM": "xterm-256color", "COLUMNS": "4000"}
    session._proc = runner.popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=session._tmp, env=env,
                                 start_new_session=True, close_fds=True)  # fmt: skip
    os.close(slave)
    session._master = master
    with _lock:
        _sessions[session.id] = session
    threading.Thread(target=_read, args=(session,), daemon=True, name=f"login-{provider}").start()
    threading.Thread(target=_watch, args=(session, settings), daemon=True, name=f"login-watch-{provider}").start()
    return session


def get(session_id: str) -> LoginSession:
    session = _sessions.get(session_id)
    if session is None:
        raise NotFoundError("No such sign-in (it may have finished or the app restarted)")
    return session


def submit(session_id: str, code: str) -> LoginSession:
    session = get(session_id)
    code = code.strip()
    if not code or any(c.isspace() for c in code):
        raise InvalidInputError("Paste the code exactly as shown (one piece, no spaces)")
    if session.state not in ("waiting", "starting") or session._master is None:
        raise InvalidInputError(f"This sign-in is {session.state}; start a new one")
    os.write(session._master, code.encode() + b"\r")
    session.state = "verifying"
    return session


def cancel(session_id: str) -> LoginSession:
    session = get(session_id)
    _stop(session, "cancelled")
    return session


def _read(session: LoginSession) -> None:
    hosts = URL_HOSTS[session.provider]
    while session._master is not None:
        try:
            chunk = os.read(session._master, 4096)
        except OSError:
            break
        if not chunk:
            break
        session.output = (session.output + ANSI_RE.sub("", chunk.decode(errors="replace")))[-20000:]
        if session.url is None:
            for url in URL_RE.findall(session.output.replace("\n", " ")):
                if any(h in url for h in hosts):
                    session.url = url
                    if session.state == "starting":
                        session.state = "waiting"
                    break


def _watch(session: LoginSession, settings: Settings) -> None:
    provider = get_provider(session.provider, settings)
    while session.state in ("starting", "waiting", "verifying"):
        time.sleep(1.5)
        # Signing in again: the old login still counts as signed in, so wait for the tool to finish instead
        signed, account = (False, "") if session.relogin else provider.signed_in()
        if signed:
            session.account = account
            _stop(session, "done")
            return
        proc = session._proc
        if proc is not None and proc.poll() is not None:
            time.sleep(1.0)  # let the credentials land before the last check
            signed, account = provider.signed_in()
            if signed:
                session.account = account
                _stop(session, "done")
            else:
                session.error = _reason(session.output) or f"{session.provider} sign-in ended without signing in"
                _stop(session, "failed")
            return
        if time.time() - session.started > TIMEOUT_SECONDS:
            session.error = "Timed out waiting for the code; start again and paste it within a few minutes"
            _stop(session, "failed")
            return


def _reason(output: str) -> str | None:
    for pattern in (r"invalid_grant[^\n]*", r"Failed to authenticate[^\n]*", r"[Ee]rror[^\n]{0,160}"):
        if m := re.search(pattern, output):
            text = m.group(0).strip()
            if "invalid_grant" in text:
                return "The code was rejected (expired, already used, or from an older link). Start again and paste the new code."
            return text
    return None


def _stop(session: LoginSession, state: str) -> None:
    if session._master is None and session.state in ("done", "failed", "cancelled"):
        return  # already stopped (cancel and the watcher can race)
    session.state = state
    if session.provider == "gemini-cli":  # put back a login set aside by "Sign in again" unless a new one landed
        creds = GEMINI_CREDS.expanduser()
        backup = creds.with_name(creds.name + ".assetforge-backup")
        if backup.exists():
            backup.replace(creds) if not creds.exists() else backup.unlink()
    proc, master = session._proc, session._master
    session._master = None
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    if master is not None:
        try:
            os.close(master)
        except OSError:
            pass
    if session._tmp:
        shutil.rmtree(session._tmp, ignore_errors=True)
