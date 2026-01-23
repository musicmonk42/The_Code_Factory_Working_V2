"""
Unit Tests for Agent Dependency Graph
======================================

Test suite for agent_dependency_graph module ensuring correct phased loading
configuration and dependency validation.

**Module Version**: 1.0.0
**Author**: Code Factory Platform Team
**Last Updated**: 2026-01-23
"""
import pytest
from server.utils.agent_dependency_graph import (
    AgentConfig,
    AGENT_GRAPH,
    get_load_phases,
    get_agent_config,
    validate_dependency_graph,
)


class TestAgentConfig:
    """Test cases for AgentConfig dataclass."""
    
    def test_create_agent_config(self):
        """Test creating a basic AgentConfig."""
        config = AgentConfig(
            name="test_agent",
            module_path="test.module",
            dependencies=(),
            phase=1
        )
        
        assert config.name == "test_agent"
        assert config.module_path == "test.module"
        assert config.dependencies == ()
        assert config.phase == 1
    
    def test_immutability(self):
        """Test that AgentConfig is immutable (frozen)."""
        config = AgentConfig(
            name="test_agent",
            module_path="test.module",
            dependencies=(),
            phase=1
        )
        
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            config.name = "different_name"
    
    def test_dependencies_as_tuple(self):
        """Test that dependencies are converted to tuple."""
        config = AgentConfig(
            name="test_agent",
            module_path="test.module",
            dependencies=("dep1", "dep2"),
            phase=2
        )
        
        assert isinstance(config.dependencies, tuple)
        assert config.dependencies == ("dep1", "dep2")
    
    def test_dependency_count_property(self):
        """Test dependency_count property."""
        config = AgentConfig(
            name="test_agent",
            module_path="test.module",
            dependencies=("dep1", "dep2", "dep3"),
            phase=2
        )
        
        assert config.dependency_count == 3
    
    def test_has_dependencies_property(self):
        """Test has_dependencies property."""
        config_with_deps = AgentConfig(
            name="test_agent",
            module_path="test.module",
            dependencies=("dep1",),
            phase=2
        )
        
        config_no_deps = AgentConfig(
            name="test_agent",
            module_path="test.module",
            dependencies=(),
            phase=1
        )
        
        assert config_with_deps.has_dependencies is True
        assert config_no_deps.has_dependencies is False
    
    def test_validation_empty_name(self):
        """Test validation rejects empty name."""
        with pytest.raises(ValueError, match="Agent name cannot be empty"):
            AgentConfig(
                name="",
                module_path="test.module",
                dependencies=(),
                phase=1
            )
    
    def test_validation_empty_module_path(self):
        """Test validation rejects empty module path."""
        with pytest.raises(ValueError, match="Module path.*cannot be empty"):
            AgentConfig(
                name="test_agent",
                module_path="",
                dependencies=(),
                phase=1
            )
    
    def test_validation_negative_phase(self):
        """Test validation rejects negative phase."""
        with pytest.raises(ValueError, match="Phase.*must be non-negative"):
            AgentConfig(
                name="test_agent",
                module_path="test.module",
                dependencies=(),
                phase=-1
            )


class TestAgentGraph:
    """Test cases for AGENT_GRAPH configuration."""
    
    def test_all_agents_present(self):
        """Test that all expected agents are in the graph."""
        expected_agents = {"codegen", "testgen", "deploy", "critique", "docgen"}
        actual_agents = set(AGENT_GRAPH.keys())
        
        assert actual_agents == expected_agents
    
    def test_all_configs_valid(self):
        """Test that all agent configs are valid."""
        for name, config in AGENT_GRAPH.items():
            assert isinstance(config, AgentConfig)
            assert config.name == name
            assert len(config.module_path) > 0
            assert config.phase >= 1
    
    def test_phase_1_has_no_dependencies(self):
        """Test that Phase 1 agents have no dependencies."""
        phase_1_agents = [agent for agent in AGENT_GRAPH.values() if agent.phase == 1]
        
        for agent in phase_1_agents:
            assert len(agent.dependencies) == 0, \
                f"Phase 1 agent '{agent.name}' should have no dependencies"
    
    def test_dependencies_exist(self):
        """Test that all dependencies reference existing agents."""
        for agent in AGENT_GRAPH.values():
            for dep in agent.dependencies:
                assert dep in AGENT_GRAPH, \
                    f"Agent '{agent.name}' depends on non-existent '{dep}'"
    
    def test_dependencies_in_earlier_phases(self):
        """Test that dependencies are always in earlier phases."""
        for agent in AGENT_GRAPH.values():
            for dep_name in agent.dependencies:
                dep_agent = AGENT_GRAPH[dep_name]
                assert dep_agent.phase < agent.phase, \
                    f"Agent '{agent.name}' (phase {agent.phase}) depends on " \
                    f"'{dep_name}' (phase {dep_agent.phase})"
    
    def test_no_circular_dependencies(self):
        """Test that there are no circular dependencies."""
        def has_circular_dep(agent_name, visited, rec_stack):
            visited.add(agent_name)
            rec_stack.add(agent_name)
            
            agent = AGENT_GRAPH[agent_name]
            for dep in agent.dependencies:
                if dep not in visited:
                    if has_circular_dep(dep, visited, rec_stack):
                        return True
                elif dep in rec_stack:
                    return True
            
            rec_stack.remove(agent_name)
            return False
        
        visited = set()
        for agent_name in AGENT_GRAPH:
            if agent_name not in visited:
                assert not has_circular_dep(agent_name, visited, set()), \
                    f"Circular dependency detected involving '{agent_name}'"


class TestGetLoadPhases:
    """Test cases for get_load_phases() function."""
    
    def test_returns_dict(self):
        """Test that get_load_phases returns a dictionary."""
        phases = get_load_phases()
        assert isinstance(phases, dict)
    
    def test_all_agents_included(self):
        """Test that all agents are included in phases."""
        phases = get_load_phases()
        all_agents_in_phases = set()
        
        for agent_list in phases.values():
            all_agents_in_phases.update(agent.name for agent in agent_list)
        
        assert all_agents_in_phases == set(AGENT_GRAPH.keys())
    
    def test_phases_are_sequential(self):
        """Test that phases are numbered sequentially."""
        phases = get_load_phases()
        phase_numbers = sorted(phases.keys())
        
        assert phase_numbers[0] == 1  # Should start at 1
        for i in range(len(phase_numbers) - 1):
            assert phase_numbers[i+1] == phase_numbers[i] + 1
    
    def test_agents_in_correct_phases(self):
        """Test that agents are grouped into correct phases."""
        phases = get_load_phases()
        
        for phase_num, agents in phases.items():
            for agent in agents:
                assert agent.phase == phase_num, \
                    f"Agent '{agent.name}' in phase {phase_num} but has phase={agent.phase}"
    
    def test_codegen_in_phase_1(self):
        """Test that codegen is in phase 1."""
        phases = get_load_phases()
        phase_1_names = [agent.name for agent in phases[1]]
        
        assert "codegen" in phase_1_names


class TestGetAgentConfig:
    """Test cases for get_agent_config() function."""
    
    def test_get_existing_agent(self):
        """Test getting config for existing agent."""
        config = get_agent_config("codegen")
        
        assert isinstance(config, AgentConfig)
        assert config.name == "codegen"
    
    def test_get_nonexistent_agent(self):
        """Test that getting non-existent agent raises KeyError."""
        with pytest.raises(KeyError, match="not found in dependency graph"):
            get_agent_config("nonexistent_agent")
    
    def test_invalid_agent_name_empty(self):
        """Test that empty agent name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid agent name"):
            get_agent_config("")
    
    def test_invalid_agent_name_none(self):
        """Test that None agent name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid agent name"):
            get_agent_config(None)
    
    def test_all_agents_retrievable(self):
        """Test that all agents can be retrieved."""
        for agent_name in AGENT_GRAPH.keys():
            config = get_agent_config(agent_name)
            assert config.name == agent_name


class TestValidateDependencyGraph:
    """Test cases for validate_dependency_graph() function."""
    
    def test_current_graph_valid(self):
        """Test that current AGENT_GRAPH is valid."""
        errors = validate_dependency_graph()
        
        assert isinstance(errors, list)
        assert len(errors) == 0, f"Dependency graph has errors: {errors}"
    
    def test_returns_empty_list_on_success(self):
        """Test that validation returns empty list when successful."""
        errors = validate_dependency_graph()
        assert errors == []


@pytest.mark.integration
class TestRealWorldScenarios:
    """Integration tests for real-world scenarios."""
    
    def test_loading_order(self):
        """Test that loading order respects dependencies."""
        phases = get_load_phases()
        loaded = set()
        
        for phase_num in sorted(phases.keys()):
            # Check that all dependencies of agents in this phase are already loaded
            for agent in phases[phase_num]:
                for dep in agent.dependencies:
                    assert dep in loaded, \
                        f"Agent '{agent.name}' depends on '{dep}' but it's not loaded yet"
            
            # Mark agents in this phase as loaded
            for agent in phases[phase_num]:
                loaded.add(agent.name)
    
    def test_parallel_loading_safe_within_phase(self):
        """Test that agents within same phase can be loaded in parallel."""
        phases = get_load_phases()
        
        for phase_num, agents in phases.items():
            # Check that no agent in this phase depends on another agent in the same phase
            agent_names = {agent.name for agent in agents}
            
            for agent in agents:
                deps_in_same_phase = set(agent.dependencies) & agent_names
                assert len(deps_in_same_phase) == 0, \
                    f"Agent '{agent.name}' depends on '{deps_in_same_phase}' in same phase"
    
    def test_minimum_phases_used(self):
        """Test that agents are in the earliest possible phase."""
        # This is implicitly tested by phase assignment rules, but good to verify
        phases = get_load_phases()
        
        for phase_num, agents in phases.items():
            for agent in agents:
                # Check if agent could be in an earlier phase
                if agent.dependencies:
                    max_dep_phase = max(
                        AGENT_GRAPH[dep].phase for dep in agent.dependencies
                    )
                    expected_phase = max_dep_phase + 1
                    assert agent.phase == expected_phase, \
                        f"Agent '{agent.name}' could be in phase {expected_phase} " \
                        f"but is in phase {agent.phase}"
