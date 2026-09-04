from .core import *  # noqa: F401,F403


def _close_session(self, key: str) -> None:
    session = self.registry.remove(key)
    if session is not None:
        self.transport.close_session(session)


BridgeRuntime.close_session = _close_session
