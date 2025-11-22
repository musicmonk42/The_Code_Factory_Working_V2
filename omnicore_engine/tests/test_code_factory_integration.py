import pytest
from omnicore_engine.engines import OmniCoreOmega
from omnicore_engine.plugin_registry import PluginRegistry
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_import_fixer_engine(mocker):
    mocker.patch(
        "self_fixing_engineer.import_fixer.import_fixer_engine.ImportFixerEngine.fix_code",
        AsyncMock(return_value="import os\n# Fixed code"),
    )
    engine = OmniCoreOmega()
    result = await engine.handle_shif_request({"code": "import bad"})
    assert result == "import os\n# Fixed code"


@pytest.mark.asyncio
async def test_generator_plugin_creation(tmp_path):
    registry = PluginRegistry()
    generator = MagicMock()
    generator.generate_plugin = MagicMock(
        return_value="""
from omnicore_engine.plugin_registry import plugin, PlugInKind
@plugin(kind=PlugInKind.SIMULATION_RUNNER, name="gen_plugin", version="1.0.0")
def gen_plugin(data): return {"result": data}
"""
    )
    plugin_file = tmp_path / "gen_plugin.py"
    plugin_file.write_text(generator.generate_plugin())
    await registry.load_from_directory(str(tmp_path))
    plugin = registry.get("SIMULATION_RUNNER", "gen_plugin")
    assert plugin({"data": "test"}) == {"result": "test"}


@pytest.mark.asyncio
async def test_self_fixing_engineer_plugin_execution(mocker):
    mocker.patch(
        "self_fixing_engineer.test_generation.gen_plugins",
        AsyncMock(return_value={"result": "test"}),
    )
    registry = PluginRegistry()
    await registry.load_from_directory("mock_dir")
    plugin = registry.get("SIMULATION_RUNNER", "gen_plugin")
    result = await plugin.execute(action="simulate", data="test")
    assert result == {"result": "test"}
