from types import TracebackType
from typing import TypeVar, overload

_E = TypeVar("_E")

class Failure(BaseException):
    def __init__(
        self,
        exc_value: BaseException | None = ...,
        exc_type: type[BaseException] | None = ...,
        exc_tb: TracebackType | None = ...,
        captureVars: bool = ...,
    ): ...
    @overload
    def check(self, singleErrorType: type[_E]) -> _E | None: ...
    @overload
    def check(self, *errorTypes: str | type[Exception]) -> Exception | None: ...
    def getTraceback(
        self,
        elideFrameworkCode: int = ...,
        detail: str = ...,
    ) -> str: ...
