"""
tests/test_tasks.py

Unit tests for tasks.py.
Task is patched to avoid pydantic validation of mock Agent objects.
We capture constructor kwargs to verify agent assignment and context chaining.

Run with: uv run pytest tests/test_tasks.py -v
"""

import pytest
from unittest.mock import patch, MagicMock


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_mock_agents():
    return {
        "manager": MagicMock(name="manager"),
        "developer": MagicMock(name="developer"),
        "tester": MagicMock(name="tester"),
        "critic": MagicMock(name="critic"),
    }


def _create_tasks_with_mock(legacy="./legacy", output="./output/App"):
    """
    Calls create_tasks() with Task patched.
    Returns (task_list, list_of_task_call_kwargs).
    """
    created = []

    def task_factory(**kw):
        t = MagicMock()
        t._kw = kw
        created.append(t)
        return t

    agents = _make_mock_agents()

    with patch("tasks.Task", side_effect=task_factory):
        from tasks import create_tasks
        result = create_tasks(agents, legacy, output)

    return result, created, agents


# ──────────────────────────────────────────────
# Task count and return type
# ──────────────────────────────────────────────

class TestCreateTasksStructure:
    def test_returns_list_of_five_tasks(self):
        result, _, _ = _create_tasks_with_mock()
        assert isinstance(result, list)
        assert len(result) == 5

    def test_five_task_instances_created(self):
        _, created, _ = _create_tasks_with_mock()
        assert len(created) == 5


# ──────────────────────────────────────────────
# Agent assignment per task
# ──────────────────────────────────────────────

class TestTaskAgentAssignment:
    def test_task1_analyze_assigned_to_developer(self):
        _, created, agents = _create_tasks_with_mock()
        assert created[0]._kw["agent"] is agents["developer"]

    def test_task2_migrate_assigned_to_developer(self):
        _, created, agents = _create_tasks_with_mock()
        assert created[1]._kw["agent"] is agents["developer"]

    def test_task3_test_assigned_to_tester(self):
        _, created, agents = _create_tasks_with_mock()
        assert created[2]._kw["agent"] is agents["tester"]

    def test_task4_review_assigned_to_critic(self):
        _, created, agents = _create_tasks_with_mock()
        assert created[3]._kw["agent"] is agents["critic"]

    def test_task5_report_assigned_to_manager(self):
        _, created, agents = _create_tasks_with_mock()
        assert created[4]._kw["agent"] is agents["manager"]


# ──────────────────────────────────────────────
# Context chaining
# ──────────────────────────────────────────────

class TestTaskContextChaining:
    def test_task1_has_no_context(self):
        _, created, _ = _create_tasks_with_mock()
        assert created[0]._kw.get("context") is None

    def test_task2_migrate_depends_on_task1_analyze(self):
        _, created, _ = _create_tasks_with_mock()
        assert created[0] in created[1]._kw["context"]

    def test_task3_test_depends_on_task2_migrate(self):
        _, created, _ = _create_tasks_with_mock()
        assert created[1] in created[2]._kw["context"]

    def test_task4_review_depends_on_task2_migrate(self):
        _, created, _ = _create_tasks_with_mock()
        assert created[1] in created[3]._kw["context"]

    def test_task5_report_depends_on_all_four_tasks(self):
        _, created, _ = _create_tasks_with_mock()
        report_context = created[4]._kw["context"]
        assert created[0] in report_context  # analyze
        assert created[1] in report_context  # migrate
        assert created[2] in report_context  # test
        assert created[3] in report_context  # review

    def test_task5_report_has_exactly_four_context_items(self):
        _, created, _ = _create_tasks_with_mock()
        assert len(created[4]._kw["context"]) == 4


# ──────────────────────────────────────────────
# Path interpolation in descriptions
# ──────────────────────────────────────────────

class TestTaskDescriptions:
    def test_task1_description_contains_legacy_path(self):
        _, created, _ = _create_tasks_with_mock(legacy="./my_project")
        assert "./my_project" in created[0]._kw["description"]

    def test_task2_description_contains_output_path(self):
        _, created, _ = _create_tasks_with_mock(output="./output/MyApp")
        assert "./output/MyApp" in created[1]._kw["description"]

    def test_task3_description_contains_output_path(self):
        _, created, _ = _create_tasks_with_mock(output="./output/MyApp")
        assert "./output/MyApp" in created[2]._kw["description"]

    def test_task4_description_contains_output_path(self):
        _, created, _ = _create_tasks_with_mock(output="./output/MyApp")
        assert "./output/MyApp" in created[3]._kw["description"]

    def test_all_tasks_have_expected_output_defined(self):
        _, created, _ = _create_tasks_with_mock()
        for i, task in enumerate(created):
            assert "expected_output" in task._kw, f"Task {i+1} missing expected_output"
            assert task._kw["expected_output"].strip(), f"Task {i+1} has empty expected_output"
