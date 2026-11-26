from typing import Any

from twisted.python.failure import Failure

EventDict = dict[str, Any]

def err(
    _stuff: None | Exception | Failure = ...,
    _why: str | None = ...,
    **kw: object,
) -> None: ...

class PythonLoggingObserver:
    def emit(self, eventDict: EventDict) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
