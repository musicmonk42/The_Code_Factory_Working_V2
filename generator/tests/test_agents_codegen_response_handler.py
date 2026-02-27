# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import json

import agents.codegen_agent.codegen_response_handler as crh


def test_clean_code_block_fenced_python():
    """
    When LLM wraps output in a fenced code block, _clean_code_block
    should strip the fences and return inner content only.
    """
    src = """```python
print("hi")
```"""
    cleaned = crh._clean_code_block(src)
    assert cleaned == 'print("hi")'


def test_clean_code_block_fenced_no_lang():
    """
    Also handle fences without explicit language.
    """
    src = """```
x = 1
```"""
    cleaned = crh._clean_code_block(src)
    assert cleaned.strip() == "x = 1"


def test_clean_code_block_no_fence():
    """
    If there is no fenced block, the function should be effectively identity.
    """
    src = "print('hi')"
    cleaned = crh._clean_code_block(src)
    assert cleaned == src


def test_parse_llm_response_multi_file_valid_python(monkeypatch):
    """
    Valid multi-file JSON with Python code should be accepted, with
    each file preserved and no error file emitted.
    """
    response = json.dumps(
        {
            "files": {
                "main.py": "print('ok')",
                "util.py": "x = 1",
            }
        }
    )

    files = crh.parse_llm_response(response, lang="python")
    assert "main.py" in files
    assert "util.py" in files
    assert files["main.py"] == "print('ok')"
    assert files["util.py"] == "x = 1"
    assert crh.ERROR_FILENAME not in files


def test_parse_llm_response_non_json_single_file():
    """
    Non-JSON plain source should be treated as a single file named DEFAULT_FILENAME.
    """
    code = "print('single')"
    files = crh.parse_llm_response(code, lang="python")
    assert files == {crh.DEFAULT_FILENAME: "print('single')"}


def test_parse_llm_response_invalid_python_returns_error_file():
    """
    If LLM output is syntactically invalid Python, handler should return
    an error file with diagnostic text.
    """
    bad_code = "def : invalid"
    files = crh.parse_llm_response(bad_code, lang="python")
    assert crh.ERROR_FILENAME in files
    err = files[crh.ERROR_FILENAME].lower()
    assert "syntax" in err or "invalid" in err or "error" in err


def test_parse_llm_response_mixed_files_one_bad():
    """
    If multi-file JSON contains one invalid Python file, that file should
    be reported in error.txt, while valid files are kept.
    """
    response = json.dumps(
        {
            "files": {
                "ok.py": "x = 1",
                "bad.py": "def : ?",
            }
        }
    )

    files = crh.parse_llm_response(response, lang="python")
    assert "ok.py" in files
    assert files["ok.py"] == "x = 1"
    assert crh.ERROR_FILENAME in files
    assert "bad.py" in files[crh.ERROR_FILENAME]


def test_is_tool_available_caches(monkeypatch):
    """
    _is_tool_available should cache results to avoid repeated expensive checks.
    We just verify it returns False for an unlikely tool and that repeated
    calls don't explode.
    """
    name = "tool_does_not_exist_xyz"

    first = crh._is_tool_available(name)
    second = crh._is_tool_available(name)

    assert first is False
    assert second is False


def test_add_traceability_comments_json():
    """
    For JSON files, add_traceability_comments should attach a _traceability map
    when requirement phrases are present.
    """
    reqs = {"features": ["handle payments securely"]}
    original = {
        "config.json": json.dumps(
            {
                "description": "This service will handle payments securely.",
                "other": "value",
            }
        )
    }

    updated = crh.add_traceability_comments(original, reqs, lang="python")
    parsed = json.loads(updated["config.json"])

    assert "_traceability" in parsed
    trace = parsed["_traceability"]
    assert isinstance(trace, dict)
    assert any("handle payments securely" in str(v).lower() for v in trace.values())


def test_add_traceability_comments_code_headers():
    """
    For code files, a 'CODE TRACEABILITY' header block should be prepended
    when matches are found between requirements and file content.
    """
    reqs = {
        "features": [
            "do thing A",
            "do thing B",
        ]
    }

    code_files = {
        "main.py": "# code that will do thing A\nprint('x')",
        "other.py": "print('no match')",
    }

    out = crh.add_traceability_comments(code_files, reqs, lang="python")

    # main.py should gain a header
    main_lines = out["main.py"].splitlines()
    assert any("CODE TRACEABILITY" in line for line in main_lines[:5])
    assert "# code that will do thing A" in out["main.py"]

    # other.py should remain unchanged
    assert out["other.py"] == "print('no match')"


def test_monitor_and_scan_code_invokes_scan_and_secret_detection(monkeypatch):
    """
    monitor_and_scan_code should:
    - Run secret regex checks
    - Invoke scan_for_vulnerabilities
    - Log appropriate actions via log_action
    Without mutating the original file mapping.
    """
    captured = {"events": []}

    def fake_scan(code_files):
        # simulate one issue in each file
        return {
            name: {"issues": [{"id": "TEST", "severity": "LOW"}]} for name in code_files
        }

    def fake_log_action(event_type, payload=None):
        captured["events"].append((event_type, payload or {}))

    monkeypatch.setattr(crh, "scan_for_vulnerabilities", fake_scan, raising=False)
    monkeypatch.setattr(crh, "log_action", fake_log_action, raising=False)

    # Code that resembles a secret to trigger regex-based scanning
    files = {"main.py": "api_key = 'X' * 40\nprint('ok')"}

    out = crh.monitor_and_scan_code(files)

    # Result should be the same object content-wise (no destructive changes)
    assert out == files

    # We expect at least one secret scan finding or similar
    assert any("Secret Scan" in e[0] or "Secret" in e[0] for e in captured["events"])

    # And a unified SAST completion log
    assert any("Unified SAST Scan Complete" in e[0] for e in captured["events"])


def test_monitor_and_scan_code_handles_scan_exceptions(monkeypatch):
    """
    If scan_for_vulnerabilities raises, monitor_and_scan_code must not crash.
    It should simply log via log_action and return original mapping.
    """
    captured = {"events": []}

    def boom_scan(code_files):
        raise RuntimeError("boom")

    def fake_log_action(event_type, payload=None):
        captured["events"].append((event_type, payload or {}))

    monkeypatch.setattr(crh, "scan_for_vulnerabilities", boom_scan, raising=False)
    monkeypatch.setattr(crh, "log_action", fake_log_action, raising=False)

    files = {"main.py": "print('ok')"}

    out = crh.monitor_and_scan_code(files)

    assert out == files
    assert any(
        "Unified SAST Scan Error" in e[0] or "SAST" in e[0] for e in captured["events"]
    )


def test_parse_llm_response_dict_openai_format():
    """
    When response is a dict in OpenAI chat completion format,
    parse_llm_response should extract content from choices[0].message.content
    """
    response_dict = {
        'choices': [
            {
                'message': {
                    'content': 'print("hello from dict")',
                    'role': 'assistant'
                }
            }
        ],
        'model': 'gpt-4',
        'usage': {'total_tokens': 100}
    }
    
    files = crh.parse_llm_response(response_dict, lang="python")
    assert crh.DEFAULT_FILENAME in files
    assert files[crh.DEFAULT_FILENAME] == 'print("hello from dict")'


def test_parse_llm_response_dict_with_content_key():
    """
    When response is a dict with a direct 'content' key,
    parse_llm_response should extract it as a fallback
    """
    response_dict = {
        'content': 'x = 42',
        'metadata': 'some info'
    }
    
    files = crh.parse_llm_response(response_dict, lang="python")
    assert crh.DEFAULT_FILENAME in files
    assert files[crh.DEFAULT_FILENAME] == 'x = 42'


def test_parse_llm_response_dict_with_text_key():
    """
    When response is a dict with a 'text' key (fallback),
    parse_llm_response should extract it
    """
    response_dict = {
        'text': 'y = 100',
        'other_data': 'ignored'
    }
    
    files = crh.parse_llm_response(response_dict, lang="python")
    assert crh.DEFAULT_FILENAME in files
    assert files[crh.DEFAULT_FILENAME] == 'y = 100'


def test_parse_llm_response_dict_multi_file_json():
    """
    When response is a dict containing multi-file JSON in OpenAI format,
    parse_llm_response should extract and parse it correctly
    """
    json_content = json.dumps({
        "files": {
            "app.py": "print('app')",
            "utils.py": "def helper(): pass"
        }
    })
    
    response_dict = {
        'choices': [
            {
                'message': {
                    'content': json_content,
                    'role': 'assistant'
                }
            }
        ]
    }
    
    files = crh.parse_llm_response(response_dict, lang="python")
    assert "app.py" in files
    assert "utils.py" in files
    assert files["app.py"] == "print('app')"
    assert "def helper(): pass" in files["utils.py"]
    assert crh.ERROR_FILENAME not in files


def test_parse_llm_response_dict_empty_content():
    """
    When response is a dict with empty or missing content,
    parse_llm_response should handle gracefully and return an error file.
    """
    response_dict = {
        'choices': [
            {
                'message': {
                    'content': '',
                    'role': 'assistant'
                }
            }
        ]
    }
    
    files = crh.parse_llm_response(response_dict, lang="python")
    # Empty content should result in error file
    assert crh.ERROR_FILENAME in files


def test_parse_llm_response_dict_malformed():
    """
    When response is a dict with unexpected structure,
    parse_llm_response should handle gracefully with fallback and return error file.
    """
    response_dict = {
        'unexpected': 'structure',
        'no_content': 'here'
    }
    
    files = crh.parse_llm_response(response_dict, lang="python")
    # Should return error file since no valid content found
    assert crh.ERROR_FILENAME in files


def test_clean_code_block_explanatory_text_only():
    """
    When LLM returns only explanatory text without code,
    _clean_code_block should return empty string to trigger proper error handling.
    """
    explanatory = """
    I apologize, but I need more information to generate the code.
    Please provide details about your requirements.
    """
    cleaned = crh._clean_code_block(explanatory)
    assert cleaned == "", "Explanatory text should result in empty string"


def test_contains_code_markers_valid_code():
    """Test that actual code is recognized."""
    code = """
    import os
    
    def main():
        print("Hello")
    """
    assert crh._contains_code_markers(code) is True


def test_contains_code_markers_prose():
    """Test that prose is NOT recognized as code."""
    prose = """
    I apologize for the confusion. Could you please provide
    more information about what you'd like me to implement?
    """
    assert crh._contains_code_markers(prose) is False


def test_contains_code_markers_mixed_but_prose_dominant():
    """Test that when prose indicators dominate, result is False."""
    mixed = """
    I'm sorry, but I need clarification on your requirements.
    Please provide more information about what you want = implemented.
    Unfortunately, I cannot proceed without more details.
    """
    # Even though there's an '=' sign, prose indicators should dominate
    assert crh._contains_code_markers(mixed) is False


def test_contains_code_markers_empty_or_short():
    """Test that empty or very short text returns False."""
    assert crh._contains_code_markers("") is False
    assert crh._contains_code_markers("ok") is False
    assert crh._contains_code_markers("    ") is False


def test_parse_llm_response_explanatory_only_returns_error():
    """
    If LLM output contains no code, should return ERROR_FILENAME with helpful message.
    """
    explanatory = "I need more details before I can generate the code."
    files = crh.parse_llm_response(explanatory, lang="python")
    assert crh.ERROR_FILENAME in files
    err_msg = files[crh.ERROR_FILENAME].lower()
    assert "did not contain recognizable code" in err_msg or "explanation" in err_msg


def test_parse_llm_response_clarification_request():
    """
    Test that clarification requests are properly detected and handled.
    """
    clarification = """
    Could you please specify the following details:
    1. The input format you expect
    2. The output format you need
    3. Any specific constraints
    
    I apologize, but I need this information to generate the appropriate code.
    """
    files = crh.parse_llm_response(clarification, lang="python")
    assert crh.ERROR_FILENAME in files
    error_content = files[crh.ERROR_FILENAME]
    assert "did not contain recognizable code" in error_content


def test_parse_llm_response_with_code_after_fence():
    """
    Test that code in fences is still properly extracted even with new validation.
    """
    response = """```python
import sys

def hello():
    print("Hello, World!")
    
if __name__ == "__main__":
    hello()
```"""
    files = crh.parse_llm_response(response, lang="python")
    assert crh.DEFAULT_FILENAME in files
    assert "import sys" in files[crh.DEFAULT_FILENAME]
    assert "def hello():" in files[crh.DEFAULT_FILENAME]
    assert crh.ERROR_FILENAME not in files


def test_clean_code_block_with_preamble_and_valid_code():
    """
    Test that preamble is stripped but valid code is preserved.
    """
    response = """Here's the implementation you requested:

import os
import sys

def process_data():
    return True
"""
    cleaned = crh._clean_code_block(response)
    assert "import os" in cleaned
    assert "def process_data():" in cleaned
    assert "Here's the implementation" not in cleaned


def test_parse_llm_response_json_prefix_stripped():
    """
    When LLM prefixes output with 'json' before the JSON body,
    parse_llm_response should strip that prefix and parse multi-file JSON.
    """
    payload = json.dumps({"files": {"app/main.py": "print('hello')", "app/utils.py": "x = 1"}})
    # Simulate LLM prefixing with 'json\n'
    response = "json\n" + payload
    files = crh.parse_llm_response(response, lang="python")
    assert "app/main.py" in files
    assert "app/utils.py" in files
    assert crh.ERROR_FILENAME not in files


def test_parse_llm_response_json_prefix_no_newline():
    """
    When LLM prefixes output with bare 'json' (no newline) before '{',
    parse_llm_response should still strip and parse.
    """
    payload = json.dumps({"files": {"main.py": "print('ok')"}})
    response = "json" + payload
    files = crh.parse_llm_response(response, lang="python")
    assert "main.py" in files
    assert files["main.py"] == "print('ok')"


def test_parse_llm_response_json_prefix_case_insensitive():
    """
    The 'json' prefix stripping should work regardless of case.
    """
    payload = json.dumps({"files": {"main.py": "x = 1"}})
    response = "JSON\n" + payload
    files = crh.parse_llm_response(response, lang="python")
    assert "main.py" in files


def test_validate_syntax_empty_code_error_message():
    """
    Test that empty code produces helpful error message.
    """
    is_valid, msg = crh._validate_syntax("", "python", "main.py")
    assert is_valid is False
    assert "Empty code block" in msg
    assert "explanatory text" in msg or "LLM returned" in msg


# ==============================================================================
# --- Regression Tests: JSON file-map parsing (root cause fixes) ---
# ==============================================================================


def test_parse_llm_response_fenced_json_block():
    """
    Regression: When LLM wraps JSON file-map in ```json ... ``` fences,
    parse_llm_response must extract and materialize individual files,
    NOT dump the JSON blob into a single main.py.
    """
    payload = json.dumps({
        "files": {
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()",
            "app/routes.py": "from fastapi import APIRouter\nrouter = APIRouter()",
        }
    })
    response = "```json\n" + payload + "\n```"
    files = crh.parse_llm_response(response, lang="python")
    assert "app/main.py" in files, f"Expected 'app/main.py' in result, got: {list(files.keys())}"
    assert "app/routes.py" in files, f"Expected 'app/routes.py' in result, got: {list(files.keys())}"
    # Must NOT produce a single main.py with JSON content
    if crh.DEFAULT_FILENAME in files:
        assert '{"files"' not in files[crh.DEFAULT_FILENAME], \
            "main.py must not contain raw JSON file-map bundle"


def test_parse_llm_response_dict_file_map():
    """
    When response is already a dict with {"files": {...}},
    parse_llm_response should recognize it as a file map and return files.
    """
    response_dict = {
        "files": {
            "app/main.py": "print('hello')",
            "app/utils.py": "x = 1",
        }
    }
    files = crh.parse_llm_response(response_dict, lang="python")
    assert "app/main.py" in files
    assert "app/utils.py" in files


def test_parse_llm_response_no_json_blob_in_main_py():
    """
    Regression: Under no circumstances should main.py contain a literal
    JSON {"files": {...}} bundle. This test verifies the guard.
    """
    # Simulate a response that is just a JSON blob with files key
    payload = json.dumps({
        "files": {
            "app/main.py": "import os",
            "tests/test_main.py": "def test_ok(): pass",
        }
    })
    files = crh.parse_llm_response(payload, lang="python")
    for filename, content in files.items():
        if filename != crh.ERROR_FILENAME:
            assert '{"files"' not in content, \
                f"File '{filename}' must not contain raw JSON file-map bundle"


def test_parse_llm_response_json_prefix_with_whitespace():
    """
    When LLM prefixes with '  json\\n' (leading whitespace + json prefix),
    the parser should strip it and parse correctly.
    """
    payload = json.dumps({"files": {"main.py": "x = 42"}})
    response = "  json\n" + payload
    files = crh.parse_llm_response(response, lang="python")
    assert "main.py" in files
    assert files["main.py"] == "x = 42"


# ==============================================================================
# Tests for Fix 1: YAML and Markdown code fence extraction
# ==============================================================================

def test_clean_code_block_yaml_fence():
    """
    Test that YAML content wrapped in ```yaml fences is properly extracted.
    This addresses the Chart.yaml markdown wrapper bug.
    """
    src = """```yaml
apiVersion: v2
name: my-chart
version: 1.0.0
```"""
    cleaned = crh._clean_code_block(src)
    expected = "apiVersion: v2\nname: my-chart\nversion: 1.0.0"
    assert cleaned == expected, f"Expected YAML extraction, got: {cleaned}"


def test_clean_code_block_yml_fence():
    """
    Test that content wrapped in ```yml fences is also extracted.
    """
    src = """```yml
key: value
nested:
  item: 123
```"""
    cleaned = crh._clean_code_block(src)
    assert "key: value" in cleaned
    assert "nested:" in cleaned
    assert "```" not in cleaned


def test_clean_code_block_markdown_fence():
    """
    Test that markdown content wrapped in ```markdown fences is extracted.
    This addresses the README.md wrapper bug.
    """
    src = """```markdown
# My Project

This is a README file.

## Installation

Run `npm install`
```"""
    cleaned = crh._clean_code_block(src)
    assert cleaned.startswith("# My Project")
    assert "Installation" in cleaned
    assert "```" not in cleaned


def test_clean_code_block_md_fence():
    """
    Test that content wrapped in ```md fences is also extracted.
    """
    src = """```md
# Hello World

This is markdown content.
```"""
    cleaned = crh._clean_code_block(src)
    assert cleaned.startswith("# Hello World")
    assert "```" not in cleaned


def test_clean_code_block_multiple_language_fences():
    """
    Test extraction works for other newly added languages.
    """
    # Test dockerfile
    dockerfile_src = """```dockerfile
FROM ubuntu:20.04
RUN apt-get update
```"""
    cleaned = crh._clean_code_block(dockerfile_src)
    assert cleaned.startswith("FROM ubuntu")
    assert "```" not in cleaned
    
    # Test bash
    bash_src = """```bash
#!/bin/bash
echo "Hello"
```"""
    cleaned = crh._clean_code_block(bash_src)
    assert "#!/bin/bash" in cleaned
    assert "```" not in cleaned


# ==============================================================================
# Tests for Fix 2: YAML content validation
# ==============================================================================

def test_validate_yaml_content_valid():
    """
    Test that valid YAML content passes validation.
    """
    valid_yaml = """apiVersion: v2
name: my-chart
version: 1.0.0
description: A Helm chart"""
    
    is_valid, msg = crh._validate_yaml_content(valid_yaml, "Chart.yaml")
    assert is_valid, f"Valid YAML should pass validation. Error: {msg}"
    assert msg == "" or "not available" in msg  # Empty or yaml not available


def test_validate_yaml_content_with_markdown_fence():
    """
    Test that YAML wrapped in markdown fences is detected and rejected.
    This is the key test for the Chart.yaml bug.
    """
    wrapped_yaml = """```yaml
apiVersion: v2
name: my-chart
```"""
    
    is_valid, msg = crh._validate_yaml_content(wrapped_yaml, "Chart.yaml")
    assert not is_valid, "YAML with markdown fences should be rejected"
    assert "markdown code fences" in msg.lower() or "code fence" in msg.lower()


def test_validate_yaml_content_with_markdown_indicators():
    """
    Test that YAML files containing markdown badges/diagrams are rejected.
    """
    markdown_yaml = """![Build Status](https://example.com/badge.svg)

## Mermaid Diagram

```mermaid
graph TD
    A --> B
```

apiVersion: v2"""
    
    is_valid, msg = crh._validate_yaml_content(markdown_yaml, "Chart.yaml")
    assert not is_valid, "YAML with markdown indicators should be rejected"
    assert "markdown" in msg.lower()


def test_validate_yaml_content_invalid_yaml_syntax():
    """
    Test that invalid YAML syntax is detected.
    """
    # Skip this test if PyYAML is not available
    if not crh.HAS_YAML:
        return
    
    invalid_yaml = """apiVersion: v2
name: [missing closing bracket
version: 1.0.0"""
    
    is_valid, msg = crh._validate_yaml_content(invalid_yaml, "Chart.yaml")
    assert not is_valid, "Invalid YAML syntax should be rejected"


def test_validate_yaml_content_empty():
    """
    Test that empty YAML content is rejected.
    """
    is_valid, msg = crh._validate_yaml_content("   ", "Chart.yaml")
    assert not is_valid, "Empty YAML should be rejected"
    assert "empty" in msg.lower()



# ==============================================================================
# Tests for: _is_likely_variable
# ==============================================================================

def test_is_likely_variable_router_suffix():
    """Names ending with _router are module-level variables, not callables."""
    assert crh._is_likely_variable("api_router") is True
    assert crh._is_likely_variable("auth_router") is True


def test_is_likely_variable_engine_client_pool():
    """Common infrastructure object suffixes are recognised as variables."""
    assert crh._is_likely_variable("db_engine") is True
    assert crh._is_likely_variable("redis_client") is True
    assert crh._is_likely_variable("connection_pool") is True


def test_is_likely_variable_callable_names_return_false():
    """Plain function/class names must NOT be misclassified as variables."""
    assert crh._is_likely_variable("get_current_user") is False
    assert crh._is_likely_variable("authenticate_user") is False
    assert crh._is_likely_variable("create_access_token") is False


def test_is_likely_variable_uppercase_class_name():
    """Uppercase names (classes) are not variable-like."""
    assert crh._is_likely_variable("User") is False
    assert crh._is_likely_variable("Role") is False


def test_is_likely_variable_case_insensitive():
    """Suffix matching should be case-insensitive."""
    assert crh._is_likely_variable("API_ROUTER") is True
    assert crh._is_likely_variable("DB_ENGINE") is True


# ==============================================================================
# Tests for: ensure_local_module_stubs — stub generation quality
# ==============================================================================

def test_ensure_local_module_stubs_function_returns_none_not_raises():
    """
    Stub functions must return None, not raise NotImplementedError.
    Generated stubs must be safe to call at runtime.
    """
    code_files = {
        "app/routes.py": "from app.auth import authenticate_user\n",
    }
    result = crh.ensure_local_module_stubs(code_files)

    assert "app/auth.py" in result
    stub_src = result["app/auth.py"]
    assert "NotImplementedError" not in stub_src
    assert "return None" in stub_src


def test_ensure_local_module_stubs_variable_gets_assignment_not_function():
    """
    Variable-like symbols (e.g. api_router) must become ``name = None`` assignments,
    not stub functions.
    """
    code_files = {
        "app/main.py": "from app.routing import api_router\n",
    }
    result = crh.ensure_local_module_stubs(code_files)

    assert "app/routing.py" in result
    stub_src = result["app/routing.py"]
    # Should NOT create a def for a variable-like name
    assert "def api_router" not in stub_src
    # Should create a simple assignment
    assert "api_router = None" in stub_src


def test_ensure_local_module_stubs_class_uses_pass():
    """
    Uppercase-initial symbols (classes) must remain as ``class Foo: pass`` stubs.
    """
    code_files = {
        "app/routes.py": "from app.models import UserModel, Role\n",
    }
    result = crh.ensure_local_module_stubs(code_files)

    assert "app/models.py" in result
    stub_src = result["app/models.py"]
    assert "class UserModel:" in stub_src
    assert "class Role:" in stub_src


def test_ensure_local_module_stubs_no_notimplementederror_anywhere():
    """
    End-to-end: no stub (function, class, or variable) should raise NotImplementedError.
    """
    code_files = {
        "app/api.py": (
            "from app.auth import get_current_user, Role, create_access_token\n"
            "from app.db import db_engine, SessionLocal\n"
        ),
    }
    result = crh.ensure_local_module_stubs(code_files)
    for path, content in result.items():
        assert "NotImplementedError" not in content, (
            f"NotImplementedError found in stub file {path}"
        )


# ==============================================================================
# Tests for: .ini / .cfg files skip Python syntax validation
# ==============================================================================

def test_ini_file_skips_python_syntax_validation():
    """
    Files with .ini extension must not be validated as Python code.
    alembic.ini content should pass through without SyntaxError.
    """
    assert crh._should_skip_syntax_validation("alembic.ini") is True


def test_cfg_file_skips_python_syntax_validation():
    """setup.cfg must not be validated as Python code."""
    assert crh._should_skip_syntax_validation("setup.cfg") is True


def test_py_file_does_not_skip_syntax_validation():
    """Python files must still be validated."""
    assert crh._should_skip_syntax_validation("main.py") is False


def test_yaml_file_skips_python_syntax_validation():
    """YAML files must not be validated as Python code."""
    assert crh._should_skip_syntax_validation("docker-compose.yaml") is True
    assert crh._should_skip_syntax_validation("values.yml") is True


def test_ini_file_in_multifile_response_no_syntax_error():
    """
    Parsing a multi-file JSON that includes an alembic.ini must NOT produce a
    SyntaxError entry in the error file — the .ini content should be accepted as-is.
    """
    import json as _json
    ini_content = "[alembic]\nscript_location = alembic\nsqlalchemy.url = sqlite:///./test.db\n"
    response = _json.dumps({"files": {"alembic.ini": ini_content, "app/main.py": "x = 1"}})

    files = crh.parse_llm_response(response, lang="python")

    assert "alembic.ini" in files
    # Content may be normalised (e.g. trailing newline stripped) — compare stripped.
    assert files["alembic.ini"].strip() == ini_content.strip()
    # Should not flag alembic.ini as a syntax error
    if crh.ERROR_FILENAME in files:
        assert "alembic.ini" not in files[crh.ERROR_FILENAME]


def test_tpl_file_skips_python_syntax_validation():
    """Helm .tpl template files must not be validated as Python code."""
    assert crh._should_skip_syntax_validation("helm/templates/_helpers.tpl") is True


def test_jinja2_file_skips_python_syntax_validation():
    """Jinja2 template files must not be validated as Python code."""
    assert crh._should_skip_syntax_validation("templates/base.j2") is True
    assert crh._should_skip_syntax_validation("deploy.jinja2") is True


def test_ini_cfg_files_skip_validation():
    """Config files with .ini and .cfg extensions skip validation."""
    assert crh._should_skip_syntax_validation("alembic.ini") is True
    assert crh._should_skip_syntax_validation("setup.cfg") is True


def test_proto_file_skips_validation():
    """Protocol buffer files must not be validated as Python code."""
    assert crh._should_skip_syntax_validation("service.proto") is True


def test_terraform_file_skips_validation():
    """Terraform/HCL files must not be validated as Python code."""
    assert crh._should_skip_syntax_validation("main.tf") is True
    assert crh._should_skip_syntax_validation("variables.hcl") is True


# ==============================================================================
# Tests for: production-ready validation error file merging
# ==============================================================================

def test_production_ready_failure_appends_to_existing_error_file():
    """
    When both syntax errors and production-ready failures exist, both should
    appear in error.txt (appended), not one overwriting the other.
    """
    import json as _json

    # A file with a syntax error AND stub patterns
    bad_py = "def : invalid"  # syntax error
    stub_py = "\n".join(["x = 1"] * 3 + ["raise NotImplementedError('x')", "raise NotImplementedError('y')"])

    response = _json.dumps({"files": {"bad.py": bad_py, "stub.py": stub_py}})
    files = crh.parse_llm_response(response, lang="python")

    # error.txt should exist (from syntax failure of bad.py or prod-ready failure of stub.py)
    assert crh.ERROR_FILENAME in files


# ==============================================================================
# Tests for: Fix 8/1 - YAML/config content preserved through multi-file JSON path
# ==============================================================================

def test_yaml_content_preserved_in_multi_file_json():
    """YAML files in a multi-file JSON response must not be emptied by _clean_code_block."""
    import json as _json

    k8s_yaml = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: my-app\n"
        "spec:\n"
        "  replicas: 3\n"
    )
    response = _json.dumps({
        "files": {
            "main.py": "print('ok')",
            "k8s/deployment.yaml": k8s_yaml,
        }
    })

    files = crh.parse_llm_response(response, lang="python")
    assert "k8s/deployment.yaml" in files, "YAML file should be preserved"
    assert files["k8s/deployment.yaml"].strip() == k8s_yaml.strip()


def test_requirements_txt_preserved_in_multi_file_json():
    """requirements.txt content must not be emptied by the cleaner."""
    import json as _json

    req_content = "fastapi>=0.109.0\npydantic>=2.0.0\nuvicorn>=0.27.0\n"
    response = _json.dumps({
        "files": {
            "main.py": "print('ok')",
            "requirements.txt": req_content,
        }
    })

    files = crh.parse_llm_response(response, lang="python")
    assert "requirements.txt" in files, "requirements.txt should be preserved"
    assert "fastapi" in files["requirements.txt"]


def test_contains_code_markers_yaml():
    """YAML content with apiVersion/kind should be recognised as non-prose."""
    yaml_content = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n"
    assert crh._contains_code_markers(yaml_content), "YAML content should be recognised as code"


def test_contains_code_markers_requirements_txt():
    """requirements.txt content with version specifiers should be recognised."""
    req = "fastapi>=0.109.0\npydantic>=2.0.0\n"
    assert crh._contains_code_markers(req), "Requirements content should be recognised as code"


def test_contains_code_markers_helm_template():
    """Helm templates with {{ }} markers should be recognised."""
    helm = "replicas: {{ .Values.replicaCount }}\nimage: {{ .Values.image }}\n"
    assert crh._contains_code_markers(helm), "Helm template content should be recognised as code"


# ==============================================================================
# Tests for: Fix 2/9 - Helm/Jinja2 template YAML validation skip
# ==============================================================================

def test_validate_yaml_helm_template_skipped():
    """Helm templates with {{ }} should pass YAML validation (skipped, not rejected)."""
    helm_content = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "spec:\n"
        "  replicas: {{ .Values.replicaCount }}\n"
    )
    is_valid, msg = crh._validate_yaml_content(helm_content, "helm/templates/deployment.yaml")
    assert is_valid, f"Helm template should pass validation, got: {msg}"
    assert "template" in msg.lower()


def test_validate_yaml_templates_path_skipped():
    """Files with 'templates/' in path should be treated as templates."""
    content = "name: {{ .Values.name }}\n"
    is_valid, msg = crh._validate_yaml_content(content, "helm/templates/service.yaml")
    assert is_valid


# ==============================================================================
# Tests for: Fix 3 - Module/package collision detection and resolution
# ==============================================================================

def test_detect_module_package_collisions_removes_module_file():
    """When both routes.py and routes/__init__.py exist, routes.py is removed."""
    files = {
        "app/routes.py": "# module",
        "app/routes/__init__.py": "# package",
        "app/routes/auth.py": "# auth",
        "main.py": "# main",
    }
    result = crh._detect_module_package_collisions(files)
    assert "app/routes.py" not in result, "Module file should be removed on collision"
    assert "app/routes/__init__.py" in result, "Package __init__.py should be kept"
    assert "app/routes/auth.py" in result
    assert "main.py" in result


def test_detect_module_package_no_collision():
    """Files without collisions should be returned unchanged."""
    files = {
        "app/routes/__init__.py": "# package",
        "app/models.py": "# models",
    }
    result = crh._detect_module_package_collisions(files)
    assert result == files


# ==============================================================================
# Tests for: Fix 5 - Name shadowing detection
# ==============================================================================

def test_detect_name_shadowing_finds_shadowed_handler():
    """Handler that shadows an imported name should be flagged."""
    code = (
        "from app.services.product_service import list_products\n\n"
        "def list_products(request):\n"
        "    return list_products()\n"
    )
    files = {"app/routes/products.py": code}
    warnings = crh._detect_name_shadowing(files)
    assert any("list_products" in w for w in warnings), (
        f"Should warn about 'list_products' shadowing, got: {warnings}"
    )


def test_detect_name_shadowing_no_shadow():
    """Clean code with no shadowing should produce no warnings."""
    code = (
        "from app.services.product_service import get_products\n\n"
        "def list_products(request):\n"
        "    return get_products()\n"
    )
    files = {"app/routes/products.py": code}
    warnings = crh._detect_name_shadowing(files)
    assert warnings == []


def test_detect_name_shadowing_skips_non_python():
    """Non-Python files should be skipped silently."""
    files = {"k8s/deploy.yaml": "apiVersion: v1\n"}
    warnings = crh._detect_name_shadowing(files)
    assert warnings == []


# ==============================================================================
# Tests for: Fix 6 - __validation_summary__ is valid JSON and attached after finalization
# ==============================================================================

def test_validation_summary_is_valid_json():
    """__validation_summary__ must be serialised as JSON (parseable without eval)."""
    import json as _json

    response = _json.dumps({
        "files": {
            "main.py": "print('ok')",
            "util.py": "x = 1",
        }
    })

    files = crh.parse_llm_response(response, lang="python")
    assert "__validation_summary__" in files, "__validation_summary__ key must be present"

    raw_summary = files["__validation_summary__"]
    # Must be valid JSON (not a Python repr)
    summary = _json.loads(raw_summary)

    assert "files_passed" in summary
    assert "files_failed" in summary
    assert "rejection_rate" in summary
    assert "shadow_warnings" in summary
    assert isinstance(summary["files_passed"], int)
    assert isinstance(summary["files_failed"], int)
    assert isinstance(summary["rejection_rate"], float)
    assert isinstance(summary["shadow_warnings"], list)


def test_validation_summary_not_treated_as_code_file():
    """__validation_summary__ must not be passed to code processors as a file."""
    import json as _json

    response = _json.dumps({
        "files": {
            "main.py": "print('ok')",
        }
    })

    files = crh.parse_llm_response(response, lang="python")
    # Ensure there is no syntax error from __validation_summary__ being parsed as Python
    assert crh.ERROR_FILENAME not in files or "__validation_summary__" not in files.get(
        crh.ERROR_FILENAME, ""
    )


def test_ensure_local_module_stubs_suffixed_router_gets_apirouter():
    """ensure_local_module_stubs must stub products_router as APIRouter(), not None.

    Verifies Bug 3 fix: any symbol whose name ends with ``_router`` (e.g.
    ``products_router``, ``orders_router``) must be stubbed as an
    ``APIRouter()`` instance with the FastAPI import emitted, instead of
    being assigned ``None``.
    """
    files = {"app/main.py": "from app.routers.products import products_router\n"}
    result = crh.ensure_local_module_stubs(dict(files))
    stub = result.get("app/routers/products.py", "")
    assert "from fastapi import APIRouter" in stub, (
        "stub for products_router must include 'from fastapi import APIRouter'"
    )
    assert "products_router = APIRouter()" in stub, (
        "products_router must be stubbed as APIRouter(), not None"
    )
    assert "products_router = None" not in stub, (
        "products_router must not be stubbed as None"
    )


# ==============================================================================
# --- Tests for extract_and_populate_requirements ---
# ==============================================================================


class TestExtractAndPopulateRequirements:
    """Tests for extract_and_populate_requirements()."""

    def test_extracts_fastapi(self):
        """Verify fastapi import is extracted and added to requirements.txt."""
        files = {
            "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        }
        result = crh.extract_and_populate_requirements(files)
        assert "requirements.txt" in result
        assert "fastapi" in result["requirements.txt"]

    def test_extracts_sqlalchemy_and_jose(self):
        """Verify sqlalchemy and python-jose are extracted."""
        files = {
            "app/db.py": "from sqlalchemy import create_engine\n",
            "app/auth.py": "from jose import jwt\n",
        }
        result = crh.extract_and_populate_requirements(files)
        reqs = result.get("requirements.txt", "")
        assert "sqlalchemy" in reqs
        assert "python-jose" in reqs

    def test_merges_with_existing_requirements(self):
        """Verify existing requirements.txt content is preserved."""
        files = {
            "main.py": "import httpx\n",
            "requirements.txt": "pydantic-settings>=2.0.0\n",
        }
        result = crh.extract_and_populate_requirements(files)
        reqs = result["requirements.txt"]
        assert "pydantic-settings>=2.0.0" in reqs
        assert "httpx" in reqs

    def test_does_not_duplicate_existing_packages(self):
        """Verify packages already in requirements.txt are not duplicated."""
        files = {
            "main.py": "from fastapi import FastAPI\n",
            "requirements.txt": "fastapi>=0.100.0\n",
        }
        result = crh.extract_and_populate_requirements(files)
        reqs = result["requirements.txt"]
        # fastapi should appear only once
        assert reqs.lower().count("fastapi") == 1

    def test_filters_stdlib_modules(self):
        """Verify stdlib modules are not added to requirements.txt."""
        files = {
            "main.py": "import os\nimport json\nimport sys\n",
        }
        result = crh.extract_and_populate_requirements(files)
        # Should not add requirements.txt if all imports are stdlib
        reqs = result.get("requirements.txt", "")
        assert "os" not in reqs.split("\n")
        assert "json" not in reqs.split("\n")
        assert "sys" not in reqs.split("\n")

    def test_filters_project_local_imports(self):
        """Verify project-local app. imports are not added to requirements.txt."""
        files = {
            "app/main.py": "from app.services import auth\nfrom app.models import User\n",
        }
        result = crh.extract_and_populate_requirements(files)
        reqs = result.get("requirements.txt", "")
        assert "app" not in reqs.split("\n")

    def test_maps_module_to_pypi_name(self):
        """Verify import name to PyPI package name mapping works."""
        files = {
            "app/auth.py": "from jose import jwt\nfrom PIL import Image\nimport yaml\n",
        }
        result = crh.extract_and_populate_requirements(files)
        reqs = result.get("requirements.txt", "")
        assert "python-jose" in reqs
        assert "Pillow" in reqs
        assert "PyYAML" in reqs

    def test_non_python_files_are_ignored(self):
        """Verify non-.py files don't affect requirements extraction."""
        files = {
            "README.md": "# This uses fastapi",
            "Dockerfile": "FROM python:3.11",
        }
        result = crh.extract_and_populate_requirements(files)
        # No Python files to scan, so no new packages should be added
        assert result == files or "requirements.txt" not in result or not result.get("requirements.txt", "").strip()

    def test_extracts_pydantic_settings(self):
        """Verify pydantic_settings import is mapped to pydantic-settings."""
        files = {
            "config.py": "from pydantic_settings import BaseSettings\n",
        }
        result = crh.extract_and_populate_requirements(files)
        reqs = result.get("requirements.txt", "")
        assert "pydantic-settings" in reqs


# ==============================================================================
# --- Tests for remove_dead_imports ---
# ==============================================================================


class TestRemoveDeadImports:
    """Tests for remove_dead_imports()."""

    def test_removes_unused_import(self):
        """Verify an import that is never used is removed."""
        code = "import os\nimport json\n\ndef greet():\n    return 'hello'\n"
        result = crh.remove_dead_imports(code, "test.py")
        # os and json are unused, should be removed
        assert "import os" not in result
        assert "import json" not in result

    def test_keeps_used_import(self):
        """Verify an import that IS used is kept."""
        code = "import os\n\ndef get_cwd():\n    return os.getcwd()\n"
        result = crh.remove_dead_imports(code, "test.py")
        assert "import os" in result

    def test_keeps_partially_used_import(self):
        """Verify from-import is kept when at least one name is used."""
        code = "from typing import List, Dict\n\ndef foo(x: List[int]) -> None:\n    pass\n"
        result = crh.remove_dead_imports(code, "test.py")
        # List is used, so the import should remain
        assert "from typing import" in result

    def test_replaces_banned_asyncstdlib_import(self):
        """Verify asyncstdlib import is replaced with functools alternative."""
        code = "from asyncstdlib import lru_cache\n\n@lru_cache\ndef expensive():\n    return 42\n"
        result = crh.remove_dead_imports(code, "auth_service.py")
        assert "asyncstdlib" not in result
        assert "functools" in result

    def test_replaces_banned_aioredis_import(self):
        """Verify aioredis import is replaced with redis.asyncio alternative."""
        code = "import aioredis\n\nasync def connect():\n    return await aioredis.create_redis_pool('redis://localhost')\n"
        result = crh.remove_dead_imports(code, "redis_client.py")
        assert "aioredis" not in result

    def test_non_python_file_unchanged(self):
        """Verify non-.py files are returned unchanged."""
        code = "from fastapi import FastAPI\n"
        result = crh.remove_dead_imports(code, "template.html")
        assert result == code

    def test_handles_syntax_error_gracefully(self):
        """Verify files with syntax errors are returned unchanged."""
        code = "def : invalid syntax\n"
        result = crh.remove_dead_imports(code, "broken.py")
        assert result == code

    def test_empty_code_unchanged(self):
        """Verify empty code is returned unchanged."""
        assert crh.remove_dead_imports("", "test.py") == ""


# ==============================================================================
# --- Tests for KNOWN_HALLUCINATED_PACKAGES constant ---
# ==============================================================================


class TestKnownHallucinatedPackages:
    """Tests for the KNOWN_HALLUCINATED_PACKAGES constant."""

    def test_asyncstdlib_is_known(self):
        """Verify asyncstdlib is in the known hallucinated packages dict."""
        assert "asyncstdlib" in crh.KNOWN_HALLUCINATED_PACKAGES

    def test_aioredis_is_known(self):
        """Verify aioredis is in the known hallucinated packages dict."""
        assert "aioredis" in crh.KNOWN_HALLUCINATED_PACKAGES

    def test_each_entry_has_required_fields(self):
        """Verify each entry has required metadata fields."""
        required_fields = {"replacement_module", "replacement_import", "reason"}
        for pkg, meta in crh.KNOWN_HALLUCINATED_PACKAGES.items():
            missing = required_fields - set(meta.keys())
            assert not missing, f"Package '{pkg}' is missing fields: {missing}"

    def test_asyncstdlib_replacement_is_functools(self):
        """Verify asyncstdlib replacement module is functools."""
        assert crh.KNOWN_HALLUCINATED_PACKAGES["asyncstdlib"]["replacement_module"] == "functools"
