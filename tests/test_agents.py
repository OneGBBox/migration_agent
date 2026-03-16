"""
tests/test_agents.py

Unit tests for agents.py.
Agent and LLM are patched to avoid real API calls.
We verify the factory wires each agent with the correct role, tools, and settings.

Run with: uv run pytest tests/test_agents.py -v
"""

import pytest
from unittest.mock import patch, MagicMock


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_agents(model="gpt-4", **kwargs):
    """
    Calls create_all_agents() with Agent and LLM patched.
    Returns (agents_dict, list_of_agent_call_kwargs).
    """
    agent_kwargs_log = []

    def agent_factory(**kw):
        m = MagicMock()
        m._kw = kw
        agent_kwargs_log.append(kw)
        return m

    with patch("agents.LLM", return_value=MagicMock()):
        with patch("agents.Agent", side_effect=agent_factory):
            from agents import create_all_agents
            result = create_all_agents(model=model, **kwargs)

    return result, agent_kwargs_log


def _find_agent_kw(log, role_substring):
    """Return the kwargs dict for the agent whose role contains role_substring."""
    return next(kw for kw in log if role_substring.lower() in kw["role"].lower())


# ──────────────────────────────────────────────
# Return structure
# ──────────────────────────────────────────────

class TestCreateAllAgentsStructure:
    def test_returns_dict_with_four_keys(self):
        agents, _ = _make_agents()
        assert set(agents.keys()) == {"manager",
                                      "developer", "tester", "critic"}

    def test_all_values_are_non_none(self):
        agents, _ = _make_agents()
        for key, value in agents.items():
            assert value is not None, f"Agent '{key}' is None"

    def test_exactly_four_agent_instances_created(self):
        _, log = _make_agents()
        assert len(log) == 4


# ──────────────────────────────────────────────
# Manager agent config
# ──────────────────────────────────────────────

class TestManagerAgent:
    def test_manager_has_no_tools(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "manager")
        assert kw["tools"] == []

    def test_manager_allow_delegation_is_true(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "manager")
        assert kw["allow_delegation"] is True

    def test_manager_role_string(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "manager")
        assert "Manager" in kw["role"]


# ──────────────────────────────────────────────
# Developer agent config
# ──────────────────────────────────────────────

class TestDeveloperAgent:
    def test_developer_has_six_tools(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "developer")
        assert len(kw["tools"]) == 6

    def test_developer_allow_delegation_is_false(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "developer")
        assert kw["allow_delegation"] is False

    def test_developer_role_string(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "developer")
        assert "Developer" in kw["role"] or ".NET" in kw["role"]

    def test_developer_goal_contains_legacy_path(self):
        _, log = _make_agents(legacy_path="./my_legacy")
        kw = _find_agent_kw(log, "developer")
        assert "./my_legacy" in kw["goal"]

    def test_developer_goal_contains_output_path(self):
        _, log = _make_agents(output_path="./my_output")
        kw = _find_agent_kw(log, "developer")
        assert "./my_output" in kw["goal"]


# ──────────────────────────────────────────────
# Tester agent config
# ──────────────────────────────────────────────

class TestTesterAgent:
    def test_tester_has_six_tools(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "tester")
        assert len(kw["tools"]) == 6

    def test_tester_allow_delegation_is_false(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "tester")
        assert kw["allow_delegation"] is False


# ──────────────────────────────────────────────
# Critic agent config
# ──────────────────────────────────────────────

class TestCriticAgent:
    def test_critic_has_three_tools(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "reviewer")
        assert len(kw["tools"]) == 3

    def test_critic_allow_delegation_is_false(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "reviewer")
        assert kw["allow_delegation"] is False

    def test_critic_tools_are_read_only(self):
        _, log = _make_agents()
        kw = _find_agent_kw(log, "reviewer")
        tool_names = [t.name for t in kw["tools"]]
        assert "write_file" not in tool_names
        assert "run_command" not in tool_names


# ──────────────────────────────────────────────
# LLM wiring
# ──────────────────────────────────────────────

class TestLLMWiring:
    def test_llm_created_with_correct_model(self):
        with patch("agents.LLM") as mock_llm:
            with patch("agents.Agent", return_value=MagicMock()):
                from agents import create_all_agents
                create_all_agents(model="gpt-4o")
        mock_llm.assert_any_call(
            model="gpt-4o", temperature=0.1, max_tokens=8192
        )

    def test_all_agents_share_same_llm_instance(self):
        llm_instance = MagicMock()
        with patch("agents.LLM", return_value=llm_instance):
            agent_llms = []

            def agent_factory(**kw):
                agent_llms.append(kw.get("llm"))
                return MagicMock()

            with patch("agents.Agent", side_effect=agent_factory):
                from agents import create_all_agents
                create_all_agents()

        assert all(llm is llm_instance for llm in agent_llms)
