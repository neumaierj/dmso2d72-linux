"""A stand-in for Dmso2d72 that records calls instead of touching USB."""

from __future__ import annotations

from dmso2d72.device import DeviceError


class FakeDevice:
    """Records every device call as (method_name, args).

    Attributes are synthesised, so it satisfies any Dmso2d72 method without
    having to track the real class's surface.
    """

    def __init__(self, fail_after: int | None = None, product: str = "FakeScope"):
        self.calls: list[tuple[str, tuple]] = []
        self.product = product
        self._fail_after = fail_after
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def read_dmm(self):
        self.calls.append(("read_dmm", ()))
        return None

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args))
            if self._fail_after is not None and len(self.calls) > self._fail_after:
                raise DeviceError(f"fake failure on {name}")

        return record

    def method_names(self) -> list[str]:
        return [name for name, _ in self.calls]
