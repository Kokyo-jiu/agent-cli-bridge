from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Literal

from .core import BridgeSession


class SubprocessPromptTransport:
    """Keep one long-lived line-oriented CLI process per bridge session.

    The transport owns process lifecycle and stdin delivery only. Runtime events
    still come from ``TranscriptTailer`` (or another event source), so provider-
    specific transcript parsing stays separate from process management.

    ``input_mode='jsonl'`` writes one object per turn::

        {"type":"turn","session_id":"...","turn_id":"...","message":"..."}

    ``input_mode='line'`` writes the raw message followed by a newline.
    ``command`` is an argv sequence and is always launched without a shell.
    """

    def __init__(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        input_mode: Literal["jsonl", "line"] = "jsonl",
        encoding: str = "utf-8",
        terminate_timeout: float = 5.0,
        stdout: int | IO[str] | None = None,
        stderr: int | IO[str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("command must not be empty")
        if input_mode not in {"jsonl", "line"}:
            raise ValueError("input_mode must be 'jsonl' or 'line'")
        if terminate_timeout < 0:
            raise ValueError("terminate_timeout must be >= 0")

        self.command = tuple(os.fspath(part) for part in command)
        self.cwd = os.fspath(cwd) if cwd is not None else None
        self.env = dict(env or {})
        self.input_mode = input_mode
        self.encoding = encoding
        self.terminate_timeout = float(terminate_timeout)
        self.stdout = stdout
        self.stderr = stderr
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()

    def open_session(self, session: BridgeSession) -> None:
        with self._lock:
            current = self._processes.get(session.session_id)
            if current is not None and current.poll() is None:
                return

            child_env = os.environ.copy()
            child_env.update(self.env)
            child_env["AGENT_BRIDGE_SESSION_ID"] = session.session_id
            child_env["AGENT_BRIDGE_SESSION_KEY"] = session.key

            process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=child_env,
                stdin=subprocess.PIPE,
                stdout=self.stdout,
                stderr=self.stderr,
                text=True,
                encoding=self.encoding,
                bufsize=1,
                shell=False,
            )
            self._processes[session.session_id] = process
            session.provider_session_id = str(process.pid)
            session.touch()

    def send(self, session: BridgeSession, message: str, turn_id: str) -> None:
        with self._lock:
            process = self._require_running(session)
            if process.stdin is None:
                raise RuntimeError("subprocess_stdin_unavailable")

            if self.input_mode == "line":
                payload = message
            else:
                payload = json.dumps(
                    {
                        "type": "turn",
                        "session_id": session.session_id,
                        "turn_id": turn_id,
                        "message": message,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

            try:
                process.stdin.write(payload + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError("subprocess_write_failed") from exc

    def close_session(self, session: BridgeSession) -> None:
        with self._lock:
            process = self._processes.pop(session.session_id, None)
        if process is None:
            return

        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass

        if process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=self.terminate_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def is_running(self, session: BridgeSession) -> bool:
        with self._lock:
            process = self._processes.get(session.session_id)
            return bool(process is not None and process.poll() is None)

    def pid(self, session: BridgeSession) -> int | None:
        with self._lock:
            process = self._processes.get(session.session_id)
            return process.pid if process is not None and process.poll() is None else None

    def _require_running(self, session: BridgeSession) -> subprocess.Popen[str]:
        process = self._processes.get(session.session_id)
        if process is None or process.poll() is not None:
            raise RuntimeError("subprocess_not_running")
        return process
