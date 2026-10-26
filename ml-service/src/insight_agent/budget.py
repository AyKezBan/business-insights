"""Бюджет LLM в рублях: цены моделей, суточный счётчик в файле, пороги."""
import fcntl
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

RUB_STEP = Decimal("0.0001")


class BudgetExceeded(RuntimeError):
    pass


class DailyBudgetExceeded(BudgetExceeded):
    pass


class RunBudgetExceeded(BudgetExceeded):
    pass


class RunTimeout(BudgetExceeded):
    pass


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True)
class Price:
    input_per_1k: Decimal
    output_per_1k: Decimal


class PriceTable:
    """Цена за 1000 токенов в рублях. Неизвестная модель считается по консервативной цене по умолчанию."""

    def __init__(self, prices: dict[str, Price] | None = None, default: Price | None = None):
        self._prices = {_normalize(k): v for k, v in (prices or {}).items()}
        self.default = default or Price(Decimal("0.5"), Decimal("1.5"))

    @classmethod
    def from_json(cls, raw: str, default: Price) -> "PriceTable":
        data = json.loads(raw) if raw.strip() else {}
        prices = {m: Price(Decimal(str(p["in"])), Decimal(str(p["out"]))) for m, p in data.items()}
        return cls(prices, default)

    def price(self, model: str) -> Price:
        key = _normalize(model)
        for known, price in self._prices.items():
            if known and known in key:
                return price
        return self.default

    def cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        p = self.price(model)
        total = (p.input_per_1k * prompt_tokens + p.output_per_1k * completion_tokens) / 1000
        return total.quantize(RUB_STEP, ROUND_UP)

    def upper_bound(self, model: str, tokens: int) -> Decimal:
        p = self.price(model)
        return (max(p.input_per_1k, p.output_per_1k) * tokens / 1000).quantize(RUB_STEP, ROUND_UP)


@dataclass
class Reservation:
    amount: Decimal


class RubleLedger:
    """Суточный расход со сбросом в 03:00 (по умолчанию МСК), сохранённый между рестартами."""

    def __init__(
        self,
        limit: Decimal = Decimal(500),
        warn: Decimal = Decimal(350),
        stop_optional: Decimal = Decimal(450),
        state_path: Path | None = None,
        timezone: str = "Europe/Moscow",
        clock: Callable[[], datetime] | None = None,
    ):
        self.limit, self.warn, self.stop_optional = limit, warn, stop_optional
        self._path = state_path
        self._tz = ZoneInfo(timezone)
        self._clock = clock or (lambda: datetime.now(self._tz))
        self._lock = threading.Lock()
        self._inflight = Decimal(0)
        self._memory: dict = {}
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def _today(self) -> str:
        return (self._clock().astimezone(self._tz) - timedelta(hours=3)).date().isoformat()

    def _read(self) -> dict:
        if self._path and self._path.exists():
            try:
                return json.loads(self._path.read_text("utf-8"))
            except (OSError, ValueError):
                return {}
        return dict(self._memory)

    def _write(self, state: dict) -> None:
        if not self._path:
            self._memory = state
            return
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)

    def _state(self) -> dict:
        state = self._read()
        today = self._today()
        if state and "window_version" not in state:
            # Legacy calendar-day counters have no intraday evidence. Carry all
            # spend into this window once, then expire at the next 03:00 reset.
            state = {**state, "day": today, "window_version": 1}
            self._write(state)
        if state.get("day") != today:
            state = {"day": today, "spent": "0", "calls": 0, "window_version": 1}
        return state

    class _Locked:
        def __init__(self, ledger: "RubleLedger"):
            self.ledger = ledger
            self._fd = None

        def __enter__(self):
            self.ledger._lock.acquire()
            if self.ledger._path:
                self._fd = open(self.ledger._path.with_suffix(".lock"), "w")
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            return self

        def __exit__(self, *exc):
            if self._fd:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
            self.ledger._lock.release()

    @property
    def spent(self) -> Decimal:
        with self._Locked(self):
            return Decimal(self._state()["spent"])

    @property
    def calls(self) -> int:
        with self._Locked(self):
            return int(self._state()["calls"])

    def level(self) -> str:
        spent = self.spent
        return "stop" if spent >= self.stop_optional else "warn" if spent >= self.warn else "ok"

    def reserve(self, amount: Decimal) -> Reservation:
        with self._Locked(self):
            spent = Decimal(self._state()["spent"])
            if spent + self._inflight + amount > self.limit:
                raise DailyBudgetExceeded(f"Исчерпан дневной лимит {self.limit} руб. (потрачено {spent})")
            self._inflight += amount
            return Reservation(amount)

    def settle(self, reservation: Reservation | None, actual: Decimal) -> None:
        with self._Locked(self):
            if reservation:
                self._inflight -= reservation.amount
            state = self._state()
            state["spent"] = str(Decimal(state["spent"]) + actual)
            state["calls"] = int(state["calls"]) + 1
            self._write(state)

    def release(self, reservation: Reservation | None) -> None:
        if reservation:
            with self._Locked(self):
                self._inflight -= reservation.amount
