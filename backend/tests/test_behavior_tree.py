"""The behaviour-tree engine, checked against Nav2's control-node semantics."""

from __future__ import annotations

import pytest

from app.services.simulation.behavior_tree import (
    Action,
    Blackboard,
    Condition,
    Fallback,
    Inverter,
    RecoveryNode,
    RoundRobin,
    Sequence,
    Status,
    run,
)

S, F, R, K = Status.SUCCESS, Status.FAILURE, Status.RUNNING, Status.SKIPPED


def scripted(name: str, *results: Status) -> Action:
    """An action that returns ``results`` in order, then repeats the last one."""
    queue = list(results)

    def act(bb: Blackboard) -> Status:
        bb.calls.append(name)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return Action(name, act)


def board() -> Blackboard:
    return Blackboard(calls=[])


class TestSequenceAndFallback:
    def test_a_sequence_stops_at_the_first_failure(self):
        bb = board()
        tree = Sequence("seq", scripted("a", S), scripted("b", F), scripted("c", S))
        assert tree.execute(bb) is F
        assert bb.calls == ["a", "b"]

    def test_a_sequence_resumes_at_the_running_child(self):
        bb = board()
        tree = Sequence("seq", scripted("a", S), scripted("b", R, R, S))
        assert tree.execute(bb) is R
        assert tree.execute(bb) is R
        assert tree.execute(bb) is S
        assert bb.calls == ["a", "b", "b", "b"], "a ran once; b was resumed, not restarted"

    def test_a_fallback_stops_at_the_first_success(self):
        bb = board()
        tree = Fallback("fb", scripted("a", F), scripted("b", S), scripted("c", S))
        assert tree.execute(bb) is S
        assert bb.calls == ["a", "b"]

    def test_inverter(self):
        assert Inverter("not", scripted("x", S)).execute(board()) is F
        assert Inverter("not", scripted("x", F)).execute(board()) is S
        assert Inverter("not", scripted("x", R)).execute(board()) is R


class TestRecoveryNode:
    def test_success_needs_no_recovery(self):
        bb = board()
        tree = RecoveryNode("rec", 3, scripted("main", S), scripted("fix", S))
        assert tree.execute(bb) is S
        assert bb.calls == ["main"]

    def test_main_is_retried_after_each_successful_recovery(self):
        bb = board()
        tree = RecoveryNode("rec", 3, scripted("main", F, F, S), scripted("fix", S))
        assert tree.execute(bb) is S
        assert bb.calls == ["main", "fix", "main", "fix", "main"]

    def test_retries_are_bounded(self):
        """N retries is N recoveries and N+1 attempts at the main task, then failure."""
        bb = board()
        tree = RecoveryNode("rec", 2, scripted("main", F), scripted("fix", S))
        assert tree.execute(bb) is F
        assert bb.calls == ["main", "fix", "main", "fix", "main"]

    def test_a_failed_recovery_fails_immediately(self):
        bb = board()
        tree = RecoveryNode("rec", 5, scripted("main", F), scripted("fix", F))
        assert tree.execute(bb) is F
        assert bb.calls == ["main", "fix"]

    def test_zero_retries_is_the_main_task_alone(self):
        bb = board()
        tree = RecoveryNode("rec", 0, scripted("main", F), scripted("fix", S))
        assert tree.execute(bb) is F
        assert bb.calls == ["main"]

    def test_a_skipped_recovery_means_nothing_can_help(self):
        bb = board()
        tree = RecoveryNode("rec", 3, scripted("main", F), scripted("fix", K))
        assert tree.execute(bb) is F
        assert bb.calls == ["main", "fix"]

    def test_the_retry_count_resets_after_a_success(self):
        bb = board()
        tree = RecoveryNode("rec", 1, scripted("main", F, S, F, S), scripted("fix", S))
        assert tree.execute(bb) is S
        assert tree.execute(bb) is S, "a fresh navigation gets a fresh retry budget"

    def test_retries_must_be_non_negative(self):
        with pytest.raises(ValueError):
            RecoveryNode("rec", -1, scripted("main", S), scripted("fix", S))


class TestRoundRobin:
    def test_each_success_moves_on_to_the_next_child(self):
        bb = board()
        tree = RoundRobin("rr", scripted("a", S), scripted("b", S), scripted("c", S))
        for _ in range(4):
            assert tree.execute(bb) is S
        assert bb.calls == ["a", "b", "c", "a"]

    def test_a_failing_child_hands_over_within_the_same_tick(self):
        bb = board()
        tree = RoundRobin("rr", scripted("a", F), scripted("b", S))
        assert tree.execute(bb) is S
        assert bb.calls == ["a", "b"]

    def test_all_failing_is_failure(self):
        bb = board()
        tree = RoundRobin("rr", scripted("a", F), scripted("b", F))
        assert tree.execute(bb) is F
        assert bb.calls == ["a", "b"]

    def test_all_skipped_is_skipped(self):
        tree = RoundRobin("rr", scripted("a", K), scripted("b", K))
        assert tree.execute(board()) is K


class TestEscalation:
    """The behaviour Nav2's navigate-with-recovery tree is built around."""

    def test_successive_failures_get_successive_recoveries(self):
        bb = board()
        tree = RecoveryNode(
            "nav",
            3,
            scripted("main", F, F, F, S),
            RoundRobin(
                "recoveries", scripted("clear", S), scripted("spin", S), scripted("backup", S)
            ),
        )
        assert tree.execute(bb) is S
        assert bb.calls == ["main", "clear", "main", "spin", "main", "backup", "main"]

    def test_an_inapplicable_recovery_is_passed_over(self):
        """A recovery that fails because it cannot apply hands over in the same tick."""
        bb = board()
        tree = RecoveryNode(
            "nav",
            2,
            scripted("main", F, F, S),
            RoundRobin("recoveries", scripted("relax", F), scripted("backup", S)),
        )
        assert tree.execute(bb) is S
        assert bb.calls == ["main", "relax", "backup", "main", "relax", "backup", "main"]

    def test_gated_recovery_does_not_run_on_an_unrecoverable_error(self):
        """Nav2's WouldAPlannerRecoveryHelp: some failures no recovery can fix."""
        bb = board()
        bb.error = "LOW_BATTERY"
        tree = RecoveryNode(
            "nav",
            3,
            scripted("main", F),
            Sequence(
                "recover",
                Condition("would_help", lambda b: b.error != "LOW_BATTERY"),
                scripted("fix", S),
            ),
        )
        assert tree.execute(bb) is F
        assert bb.calls == ["main"], "the recovery never ran"


class TestRunAndTrace:
    def test_run_ticks_until_done_and_counts_ticks(self):
        bb = board()
        tree = Sequence("seq", scripted("work", R, R, R, S))
        assert run(tree, bb, max_ticks=100) is S
        assert bb.tick == 4

    def test_run_stops_at_the_tick_budget(self):
        bb = board()
        assert run(scripted("forever", R), bb, max_ticks=7) is R
        assert bb.tick == 7

    def test_transitions_are_recorded_not_every_tick(self):
        """A long RUNNING streak is one transition, not one entry per tick."""
        bb = board()
        run(Sequence("seq", scripted("work", R, R, R, S)), bb, max_ticks=100)
        work = [t for t in bb.trace if t.node == "work"]
        assert [(t.before, t.after) for t in work] == [("IDLE", "RUNNING"), ("RUNNING", "SUCCESS")]

    def test_describe_renders_the_tree(self):
        tree = RecoveryNode("nav", 2, scripted("main", S), RoundRobin("rr", scripted("a", S)))
        described = tree.describe()
        assert described["type"] == "RecoveryNode"
        assert described["retries"] == 2
        assert described["children"][1]["children"][0]["name"] == "a"
