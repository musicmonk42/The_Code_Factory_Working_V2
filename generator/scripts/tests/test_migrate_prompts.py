"""Tests for migrate_prompts.py"""
import os
import ast
import tempfile
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def test_find_prompt_dict_with_default_names():
    """Test that find_prompt_dict finds common variable names"""
    from migrate_prompts import find_prompt_dict
    
    # Test with PROMPT_TEMPLATES
    code1 = "PROMPT_TEMPLATES = {'key': 'value'}"
    tree1 = ast.parse(code1)
    result1 = find_prompt_dict(tree1)
    assert result1 is not None, "Should find PROMPT_TEMPLATES"
    var_name1, dict_node1 = result1
    assert var_name1 == "PROMPT_TEMPLATES"
    
    # Test with prompts
    code2 = "prompts = {'key': 'value'}"
    tree2 = ast.parse(code2)
    result2 = find_prompt_dict(tree2)
    assert result2 is not None, "Should find prompts"
    var_name2, dict_node2 = result2
    assert var_name2 == "prompts"
    
    # Test with TEMPLATES
    code3 = "TEMPLATES = {'key': 'value'}"
    tree3 = ast.parse(code3)
    result3 = find_prompt_dict(tree3)
    assert result3 is not None, "Should find TEMPLATES"
    var_name3, dict_node3 = result3
    assert var_name3 == "TEMPLATES"


def test_find_prompt_dict_with_custom_names():
    """Test that find_prompt_dict accepts custom variable names"""
    from migrate_prompts import find_prompt_dict
    
    code = "MY_CUSTOM_PROMPTS = {'key': 'value'}"
    tree = ast.parse(code)
    
    # Should not find with default names
    result_default = find_prompt_dict(tree)
    assert result_default is None, "Should not find custom name without specifying it"
    
    # Should find with custom names
    result_custom = find_prompt_dict(tree, var_names=["MY_CUSTOM_PROMPTS"])
    assert result_custom is not None, "Should find custom name when specified"
    var_name, dict_node = result_custom
    assert var_name == "MY_CUSTOM_PROMPTS"


def test_find_prompt_dict_returns_none_for_non_dict():
    """Test that find_prompt_dict returns None for non-dict assignments"""
    from migrate_prompts import find_prompt_dict
    
    code = "PROMPT_TEMPLATES = [1, 2, 3]"
    tree = ast.parse(code)
    result = find_prompt_dict(tree)
    assert result is None, "Should return None for non-dict"


def test_generate_loader_code_preserves_var_name():
    """Test that generate_loader_code uses the correct variable name"""
    from migrate_prompts import generate_loader_code
    
    # Test with default name
    code1 = generate_loader_code("templates", "PROMPT_TEMPLATES")
    assert "PROMPT_TEMPLATES = _load_prompt_templates_from_disk()" in code1
    
    # Test with custom name
    code2 = generate_loader_code("templates", "prompts")
    assert "prompts = _load_prompt_templates_from_disk()" in code2


def test_extract_prompts_from_dict():
    """Test prompt extraction from dictionary"""
    from migrate_prompts import extract_prompts_from_dict
    
    code = """
PROMPT_TEMPLATES = {
    'prompt1': 'This is prompt one',
    'prompt2': 'This is prompt two'
}
"""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            prompts = extract_prompts_from_dict(node)
            assert len(prompts) == 2, "Should extract 2 prompts"
            assert prompts[0] == ('prompt1', 'This is prompt one')
            assert prompts[1] == ('prompt2', 'This is prompt two')
            break


def test_migrate_file_with_custom_var_name():
    """Test that migrate_file works with custom variable names"""
    from migrate_prompts import migrate_file
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create source file with custom variable name
        source_file = tmpdir / "test_source.py"
        source_content = """
MY_PROMPTS = {
    'test_prompt': 'This is a test prompt'
}

def some_function():
    pass
"""
        source_file.write_text(source_content)
        
        # Create destination directory
        dest_dir = tmpdir / "templates"
        
        # Migrate with custom variable name
        report = migrate_file(
            source_file,
            dest_dir,
            dry_run=False,
            verbose=False,
            backup=True,
            var_names=["MY_PROMPTS"]
        )
        
        assert report["status"] == "success", f"Migration should succeed: {report.get('message', '')}"
        assert report["prompts_migrated"] == 1, "Should migrate 1 prompt"
        
        # Check that template file was created
        template_file = dest_dir / "test_prompt.j2"
        assert template_file.exists(), "Template file should be created"
        assert "This is a test prompt" in template_file.read_text()
        
        # Check that source was updated
        updated_content = source_file.read_text()
        assert "MY_PROMPTS = _load_prompt_templates_from_disk()" in updated_content
        assert "MY_PROMPTS = {" not in updated_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
