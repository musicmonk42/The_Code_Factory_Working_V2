# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Mutation Testing Guide

Mutation testing verifies the *quality* of the existing test suite by
injecting small, deliberate faults ("mutants") into the source code and
checking whether the tests catch them.  A mutant that is not caught by any
test is called a **survivor** and usually indicates a coverage or assertion
gap.

This project uses **[mutmut](https://mutmut.readthedocs.io/)** as the
mutation-testing tool.

---

## Quick Start

### 1. Install mutmut

`mutmut` is already listed in `requirements.txt`.  It is installed as part of
the standard development setup:

```bash
pip install -r requirements.txt
```

### 2. Run mutation tests (Makefile target)

```bash
make mutation-test
```

This is equivalent to:

```bash
export TESTING=1
mutmut run --no-progress
mutmut results
```

### 3. View detailed results

```bash
# Show all surviving mutants (not caught by tests)
mutmut results

# Show the diff for a specific mutant ID (e.g., 42)
mutmut show 42

# Apply a mutant to inspect the changed code
mutmut apply 42
# ... review / fix test ...
# Revert the mutation
mutmut unapply 42
```

---

## Configuration

Mutation targets and test runner settings live in **`mutmut.ini`** at the
repository root:

```ini
[mutmut]
paths_to_mutate=generator/main/provenance.py generator/utils/project_endpoint_analyzer.py generator/agents/testgen_agent/testgen_agent.py
runner=python -m pytest generator/tests/ -x -q --tb=short --timeout=60
tests_dir=generator/tests/
```

| Key | Purpose |
|-----|---------|
| `paths_to_mutate` | Space-separated list of source files to mutate. |
| `runner` | Shell command used to run the test suite for each mutant. |
| `tests_dir` | Directory scanned for test discovery. |

---

## Interpreting Results

After `mutmut run` finishes, `mutmut results` outputs a summary:

```
Survived mutants (4)
    ↳ mutant 12  generator/main/provenance.py:160
    ↳ mutant 31  generator/main/provenance.py:287
    ...
Killed mutants (38)
Timeout mutants (0)
Suspicious mutants (0)
```

| Status | Meaning |
|--------|---------|
| **Killed** | Tests caught this mutation — good. |
| **Survived** | Tests did *not* catch this mutation — add/improve a test. |
| **Timeout** | Test suite timed out evaluating this mutant. |
| **Suspicious** | Test suite produced unusual output. |

A healthy project targets a **kill rate of ≥ 80 %**.

---

## Fixing Surviving Mutants

1. Find the surviving mutant:
   ```bash
   mutmut show <id>
   ```
2. Understand what the mutant changes (e.g., `==` → `!=`, `>` → `>=`).
3. Add or strengthen a test that would fail on the mutated code.
4. Re-run:
   ```bash
   make mutation-test
   ```

---

## CI Integration

A GitHub Actions workflow runs mutation tests on every push to `main`
(not on pull requests, to keep CI fast):

```
.github/workflows/mutation-tests.yml
```

Results are uploaded as a workflow artifact named `mutmut-results` and
can be downloaded from the Actions tab.

---

## Scope

Currently scoped to the two most-tested source files:

| File | Reason |
|------|--------|
| `generator/main/provenance.py` | Core provenance and validation logic; high test coverage. |
| `generator/agents/testgen_agent/testgen_agent.py` | Test generation agent; critical correctness. |

To add more files, extend `paths_to_mutate` in `mutmut.ini`.
