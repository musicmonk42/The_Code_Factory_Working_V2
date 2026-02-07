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


def test_validate_syntax_empty_code_error_message():
    """
    Test that empty code produces helpful error message.
    """
    is_valid, msg = crh._validate_syntax("", "python", "main.py")
    assert is_valid is False
    assert "Empty code block" in msg
    assert "explanatory text" in msg or "LLM returned" in msg

