"""A small behaviour-tree engine with BT.CPP v4 and Nav2 control-node semantics.

Nav2 drives a robot with a behaviour tree rather than a loop because a loop has
one way to fail and a tree has a structure for what to try next. The node
semantics here follow BT.CPP v4 and Nav2's control nodes as written, including
the details that make escalation work:

* ``RecoveryNode(main, recovery)`` re-ticks ``main`` after each successful
  recovery, up to ``retries`` times, and fails the moment a recovery fails.
* ``RoundRobin`` keeps its position between ticks, so the second failure gets
  the second recovery rather than the first one again.
* ``halt_child`` only halts a child that is RUNNING. A RoundRobin that just
  returned SUCCESS is not running, so the RecoveryNode resetting it does not
  rewind it - which is the whole reason recoveries escalate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Status(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    SKIPPED = "SKIPPED"


@dataclass(slots=True)
class Transition:
    tick: int
    node: str
    before: str
    after: str


class Blackboard:
    """Shared state the nodes read and write. ``tick`` is maintained by ``run``."""

    def __init__(self, **values: Any) -> None:
        self.tick = 0
        self.trace: list[Transition] = []
        self.__dict__.update(values)


class Node:
    def __init__(self, name: str) -> None:
        self.name = name
        self.status = Status.IDLE

    def tick(self, bb: Blackboard) -> Status:
        raise NotImplementedError

    def execute(self, bb: Blackboard) -> Status:
        status = self.tick(bb)
        if status is not self.status:
            bb.trace.append(Transition(bb.tick, self.name, self.status.value, status.value))
        self.status = status
        return status

    def halt(self) -> None:
        """Interrupt and reset internal state. Called only on a RUNNING node."""
        self.status = Status.IDLE

    def reset_status(self) -> None:
        self.status = Status.IDLE

    def describe(self) -> dict:
        return {"type": type(self).__name__, "name": self.name}


class Action(Node):
    def __init__(self, name: str, fn: Callable[[Blackboard], Status]) -> None:
        super().__init__(name)
        self.fn = fn

    def tick(self, bb: Blackboard) -> Status:
        return self.fn(bb)


class Condition(Node):
    def __init__(self, name: str, predicate: Callable[[Blackboard], bool]) -> None:
        super().__init__(name)
        self.predicate = predicate

    def tick(self, bb: Blackboard) -> Status:
        return Status.SUCCESS if self.predicate(bb) else Status.FAILURE


class Inverter(Node):
    def __init__(self, name: str, child: Node) -> None:
        super().__init__(name)
        self.child = child

    def tick(self, bb: Blackboard) -> Status:
        status = self.child.execute(bb)
        if status is Status.SUCCESS:
            return Status.FAILURE
        if status is Status.FAILURE:
            return Status.SUCCESS
        return status

    def halt(self) -> None:
        if self.child.status is Status.RUNNING:
            self.child.halt()
        self.child.reset_status()
        super().halt()

    def describe(self) -> dict:
        return {**super().describe(), "children": [self.child.describe()]}


class ControlNode(Node):
    def __init__(self, name: str, *children: Node) -> None:
        super().__init__(name)
        self.children = list(children)
        self.current = 0

    def halt_child(self, index: int) -> None:
        # BT.CPP v4: only a RUNNING child is halted; any other is just reset.
        child = self.children[index]
        if child.status is Status.RUNNING:
            child.halt()
        child.reset_status()

    def halt_children(self) -> None:
        for index in range(len(self.children)):
            self.halt_child(index)

    def halt(self) -> None:
        self.halt_children()
        self.current = 0
        super().halt()

    def describe(self) -> dict:
        return {**super().describe(), "children": [c.describe() for c in self.children]}


class Sequence(ControlNode):
    """Children in order; the first FAILURE fails the sequence and rewinds it."""

    def tick(self, bb: Blackboard) -> Status:
        while self.current < len(self.children):
            status = self.children[self.current].execute(bb)
            if status is Status.RUNNING:
                return Status.RUNNING
            if status is Status.FAILURE:
                self.halt_children()
                self.current = 0
                return Status.FAILURE
            self.current += 1
        self.halt_children()
        self.current = 0
        return Status.SUCCESS


class Fallback(ControlNode):
    """Children in order until one succeeds."""

    def tick(self, bb: Blackboard) -> Status:
        while self.current < len(self.children):
            status = self.children[self.current].execute(bb)
            if status is Status.RUNNING:
                return Status.RUNNING
            if status is Status.SUCCESS:
                self.halt_children()
                self.current = 0
                return Status.SUCCESS
            self.current += 1
        self.halt_children()
        self.current = 0
        return Status.FAILURE


class RecoveryNode(ControlNode):
    """Nav2's RecoveryNode: retry ``main`` after each successful ``recovery``."""

    def __init__(self, name: str, retries: int, main: Node, recovery: Node) -> None:
        super().__init__(name, main, recovery)
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.retries = retries
        self.retry_count = 0

    def tick(self, bb: Blackboard) -> Status:
        while self.current < 2 and self.retry_count <= self.retries:
            status = self.children[self.current].execute(bb)

            if self.current == 0:
                if status is Status.SKIPPED:
                    self.halt()
                    return Status.SKIPPED
                if status is Status.SUCCESS:
                    self.halt_child(1)
                    self.halt()
                    return Status.SUCCESS
                if status is Status.RUNNING:
                    return Status.RUNNING
                if self.retry_count < self.retries:
                    self.halt_child(0)
                    self.current = 1
                    continue
                self.halt()
                return Status.FAILURE

            if status is Status.SKIPPED:
                # A recovery that cannot apply is not a retry; nothing can help.
                self.current = 0
                self.halt_child(1)
                return Status.FAILURE
            if status is Status.RUNNING:
                return Status.RUNNING
            if status is Status.SUCCESS:
                self.halt_child(1)
                self.retry_count += 1
                self.current = 0
                continue
            self.halt()
            return Status.FAILURE

        self.halt()
        return Status.FAILURE

    def halt(self) -> None:
        super().halt()
        self.retry_count = 0

    def describe(self) -> dict:
        return {**super().describe(), "retries": self.retries}


class RoundRobin(ControlNode):
    """Nav2's RoundRobin: each call starts at the child after the last one ticked."""

    def __init__(self, name: str, *children: Node, wrap_around: bool = True) -> None:
        super().__init__(name, *children)
        self.wrap_around = wrap_around
        self.failed = 0

    def tick(self, bb: Blackboard) -> Status:
        count = len(self.children)
        skipped = 0
        while self.failed + skipped < count:
            status = self.children[self.current].execute(bb)
            if status is not Status.RUNNING:
                self.current += 1
                if self.current == count:
                    if self.wrap_around:
                        self.current = 0
                    else:
                        if status is Status.SKIPPED:
                            skipped += 1
                        elif status is Status.FAILURE:
                            self.failed += 1
                        break
            if status is Status.SUCCESS:
                self.failed = 0
                self.halt_children()
                return Status.SUCCESS
            if status is Status.FAILURE:
                self.failed += 1
            elif status is Status.SKIPPED:
                skipped += 1
            else:
                return Status.RUNNING

        all_skipped = skipped == count
        self.halt()
        return Status.SKIPPED if all_skipped else Status.FAILURE

    def halt(self) -> None:
        super().halt()
        self.failed = 0


def run(root: Node, bb: Blackboard, max_ticks: int) -> Status:
    """Tick ``root`` until it stops returning RUNNING, or the tick budget runs out."""
    status = Status.RUNNING
    while status is Status.RUNNING and bb.tick < max_ticks:
        status = root.execute(bb)
        bb.tick += 1
    return status
