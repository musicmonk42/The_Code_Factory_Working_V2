import json
import pytest

from agents.critique_agent.critique_linter import (  # type: ignore
    ruff_json,
    eslint_json,
    golangci_lint_json,
    clippy_json,
    checkstyle_json,
    spotbugs_json,
    LINTER_CONFIG,
)


def test_linter_config_has_core_languages():
    """
    Ensure core languages are wired and non-empty.
    This protects against accidental regression / deletion.
    """
    for lang in ("python", "javascript", "go"):
        assert lang in LINTER_CONFIG
        assert isinstance(LINTER_CONFIG[lang], list)
        assert LINTER_CONFIG[lang], f"{lang} config must not be empty"


def test_ruff_json_parsing_minimal():
    raw = json.dumps(
        [
            {
                "code": "E501",
                "severity": "warning",
                "message": "line too long",
                "filename": "main.py",
                "location": {"row": 10, "column": 1},
            }
        ]
    )
    issues = ruff_json(raw)
    assert len(issues) == 1
    i = issues[0]
    assert i["code"] == "E501"
    assert i["file"] == "main.py"
    assert i["line"] == 10


def test_eslint_json_parsing_minimal():
    raw = json.dumps(
        [
            {
                "filePath": "app.js",
                "messages": [
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 2,
                        "message": "x is defined but never used",
                        "line": 3,
                        "column": 5,
                    }
                ],
            }
        ]
    )
    issues = eslint_json(raw)
    assert len(issues) == 1
    i = issues[0]
    assert i["code"] == "no-unused-vars"
    assert i["file"].endswith("app.js")
    assert i["severity"] == "error"


def test_golangci_lint_json_parsing_minimal():
    raw = json.dumps(
        {
            "Issues": [
                {
                    "FromLinter": "errcheck",
                    "Severity": "warning",
                    "Text": "error return value not checked",
                    "Pos": {"Filename": "main.go", "Line": 5, "Column": 10},
                    "SourceLines": ["doThing()"],
                }
            ]
        }
    )
    issues = golangci_lint_json(raw)
    assert len(issues) == 1
    assert issues[0]["code"] == "errcheck"
    assert issues[0]["file"] == "main.go"


def test_clippy_json_parsing_minimal():
    line = json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "code": {"code": "clippy::unused_variable"},
                "message": "unused variable: `x`",
                "spans": [
                    {
                        "file_name": "lib.rs",
                        "line_start": 7,
                        "column_start": 9,
                        "text": ["let x = 1;"],
                    }
                ],
                "level": "warning",
            },
        }
    )
    issues = clippy_json(line)
    assert len(issues) == 1
    assert issues[0]["file"] == "lib.rs"
    assert "unused variable" in issues[0]["message"].lower()


def test_checkstyle_json_parsing_minimal():
    raw = json.dumps(
        {
            "files": [
                {
                    "name": "Main.java",
                    "errors": [
                        {
                            "severity": "warning",
                            "line": 3,
                            "column": 1,
                            "message": "Missing Javadoc",
                            "source": "com.puppycrawl.tools.checkstyle.checks.javadoc.MissingJavadocMethod",
                        }
                    ],
                }
            ]
        }
    )
    issues = checkstyle_json(raw)
    assert len(issues) == 1
    assert issues[0]["file"] == "Main.java"
    assert issues[0]["code"] == "MissingJavadocMethod"


def test_spotbugs_json_parsing_minimal():
    raw = json.dumps(
        {
            "BugCollection": {
                "BugInstance": {
                    "@type": "NP_NULL_ON_SOME_PATH",
                    "@rank": "3",
                    "LongMessage": "Possible NPE",
                    "SourceLine": {
                        "@start": "42",
                        "@sourcefile": "Main.java",
                    },
                }
            }
        }
    )
    issues = spotbugs_json(raw)
    assert len(issues) == 1
    assert issues[0]["file"] == "Main.java"
    assert issues[0]["severity"] == "critical"
