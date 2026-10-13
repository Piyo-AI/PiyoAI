"""The resource broker: hands out shared resources (run slots, model calls, the browser, scripts) by counting.

Allocation is plain code, never a model: a request names a pool, and the broker grants a lease when the pool
has room, so nothing a page or a tool result says can talk it into more. Pools:

- `runs`: background runs at once.
- `model:<provider>`: concurrent model calls to one provider (a local provider defaults to 1).
- `browser`: the signed-in browser profile, one background run at a time until per-run browsers (9C).
- `scripts`: concurrent `skill.run_script` runs.

Waiting requests are served highest priority first and oldest first within a level; a request gains one
level per `AGING_S` waiting, so a low-priority run still gets its turn. The interactive chat ranks above
every background run and has one slot kept free for it in the model and browser pools (when the pool has
room for two or more), so background work never makes the chat wait. When a run waits for `runs` or
`browser` held by lower-priority runs, the lowest one is asked to pause at its next checkpoint (soft
preemption: a tool call in progress is never interrupted).

Leases are taken where the work happens: the run slot by the agent manager, model calls in the turn function
wrapper (`leased_turn`, so the agent loop is unaware), the browser at a run's first browser tool, scripts
inside the tool's handler (after the gate). Whoever runs gets a `Holder` through `use_holder`; code without
one (a held action the user approved, tests) counts as interactive.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from piyo.config.resource_limits import Limits

RANK = {"low": 0, "normal": 1, "high": 2}
INTERACTIVE_RANK = 1000
AGING_S = 600.0  # a waiting request gains one priority level per 10 minutes
RESERVED = ("model:", "browser")  # pools that keep one slot for the chat
PREEMPTS = ("runs", "browser")  # pools where a higher-priority run asks a lower one to make room


@dataclass(eq=False)
class Holder:
    """One run that takes leases. The callbacks let the agent manager show and act on what happens."""

    id: str
    priority: str = "normal"
    interactive: bool = False
    on_wait: Callable[[str], None] | None = None  # started waiting for a pool
    on_granted: Callable[[str], None] | None = None  # got the pool after waiting
    preempt: Callable[[], None] | None = None  # asked to pause at its next checkpoint to make room
    waited: float = 0.0  # seconds spent waiting for leases; kept out of the run's timeout
    yielding: bool = False  # already asked to make room

    @property
    def rank(self) -> int:
        return INTERACTIVE_RANK if self.interactive else RANK.get(self.priority, RANK["normal"])


@dataclass(eq=False)
class Lease:
    pool: str
    holder: Holder
    released: bool = False


@dataclass(eq=False)
class _Waiter:
    holder: Holder
    since: float
    order: int
    future: asyncio.Future


@dataclass
class _Pool:
    leases: list[Lease] = field(default_factory=list)
    waiters: list[_Waiter] = field(default_factory=list)


def pool_capacity(limits: Limits, pool: str, local: bool = False) -> int:
    """The size of `pool` under `limits`; `local` says a `model:` pool's provider runs on this machine."""
    if pool == "runs":
        return limits.runs
    if pool == "scripts":
        return limits.scripts
    if pool.startswith("model:"):
        return limits.local_model_calls if local else limits.cloud_model_calls
    return 1  # `browser` until per-run browsers (workstream C), and any pool not configured yet


_holder: ContextVar[Holder | None] = ContextVar("piyo_lease_holder", default=None)


def current_holder() -> Holder:
    return _holder.get() or Holder("interactive", interactive=True)


@contextmanager
def use_holder(holder: Holder) -> Iterator[Holder]:
    token = _holder.set(holder)
    try:
        yield holder
    finally:
        _holder.reset(token)


class ResourceBroker:
    def __init__(self, capacity: Callable[[str], int], clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity, self._clock = capacity, clock
        self._pools: dict[str, _Pool] = {}
        self._held: dict[Holder, list[Lease]] = {}
        self._order = itertools.count()

    async def acquire(self, pool: str, holder: Holder | None = None) -> Lease:
        """Wait for room in `pool`. Cancelling the wait leaves nothing behind."""
        holder = holder or current_holder()
        state = self._pools.setdefault(pool, _Pool())
        waiter = _Waiter(holder, self._clock(), next(self._order), asyncio.get_running_loop().create_future())
        state.waiters.append(waiter)
        self._dispatch(pool)
        if waiter.future.done():
            return waiter.future.result()
        if holder.on_wait:
            holder.on_wait(pool)
        self._ask_to_yield(pool, holder)
        started = time.monotonic()
        try:
            lease = await waiter.future
        except asyncio.CancelledError:
            if waiter in state.waiters:
                state.waiters.remove(waiter)
            elif waiter.future.done() and not waiter.future.cancelled():
                self.release(waiter.future.result())  # granted just as it was cancelled: pass it on
            raise
        finally:
            holder.waited += time.monotonic() - started
        if holder.on_granted:
            holder.on_granted(pool)
        return lease

    def release(self, lease: Lease) -> None:
        """Give a lease back; releasing twice is harmless (the end-of-run sweep may come after the owner)."""
        if lease.released:
            return
        lease.released = True
        self._pools[lease.pool].leases.remove(lease)
        held = self._held.get(lease.holder, [])
        if lease in held:
            held.remove(lease)
        if not held:
            self._held.pop(lease.holder, None)
        self._dispatch(lease.pool)

    def release_all(self, holder: Holder) -> None:
        """The sweep at the end of a run, or of one part of it before a pause: all it holds goes back."""
        for lease in list(self._held.get(holder, [])):
            self.release(lease)
        holder.yielding = False

    @asynccontextmanager
    async def lease(self, pool: str, holder: Holder | None = None) -> AsyncIterator[Lease]:
        lease = await self.acquire(pool, holder)
        try:
            yield lease
        finally:
            self.release(lease)

    def limits_changed(self) -> None:
        """A raised limit lets waiting requests go now; a lowered one takes effect as leases come back."""
        for pool in list(self._pools):
            self._dispatch(pool)

    def usage(self) -> dict[str, dict[str, int]]:
        return {
            name: {"capacity": self._capacity(name), "used": len(p.leases), "waiting": len(p.waiters)}
            for name, p in sorted(self._pools.items())
        }

    def held(self, holder: Holder) -> list[str]:
        return [lease.pool for lease in self._held.get(holder, [])]

    # -- allocation ------------------------------------------------------------------------

    def _reserve(self, pool: str, capacity: int) -> int:
        return 1 if pool.startswith(RESERVED) and capacity >= 2 else 0

    def _effective(self, waiter: _Waiter, now: float) -> tuple[int, int]:
        rank = waiter.holder.rank
        if not waiter.holder.interactive:
            rank = min(rank + int((now - waiter.since) // AGING_S), INTERACTIVE_RANK - 1)
        return rank, -waiter.order  # oldest first within a level

    def _dispatch(self, pool: str) -> None:
        state = self._pools[pool]
        state.waiters = [w for w in state.waiters if not w.future.done()]  # cancelled waits
        capacity = self._capacity(pool)
        reserve = self._reserve(pool, capacity)
        now = self._clock()
        while state.waiters:
            best = max(state.waiters, key=lambda w: self._effective(w, now))
            # The best one cannot go, so nobody can: the chat ranks first and may use the kept slot.
            if len(state.leases) >= (capacity if best.holder.interactive else capacity - reserve):
                return
            state.waiters.remove(best)
            lease = Lease(pool, best.holder)
            state.leases.append(lease)
            self._held.setdefault(best.holder, []).append(lease)
            best.future.set_result(lease)

    def _ask_to_yield(self, pool: str, waiting: Holder) -> None:
        if not pool.startswith(PREEMPTS):
            return
        holders = [
            lease.holder
            for lease in self._pools[pool].leases
            if lease.holder.preempt and not lease.holder.interactive and lease.holder is not waiting
        ]
        if any(h.yielding for h in holders):
            return  # one is already making room
        lower = [h for h in reversed(holders) if h.rank < waiting.rank]  # newest first among equals
        if lower:
            victim = min(lower, key=lambda h: h.rank)
            victim.yielding = True
            victim.preempt()  # type: ignore[misc]


def leased_turn(turn: Callable[..., AsyncIterator], broker: ResourceBroker) -> Callable[..., AsyncIterator]:
    """Wrap a turn function so each model call holds a `model:<provider>` lease while it streams."""

    async def run(provider, name, messages, specs, system, max_tokens):
        async with broker.lease(f"model:{provider.id}"):
            async for event in turn(provider, name, messages, specs, system, max_tokens):
                yield event

    return run
