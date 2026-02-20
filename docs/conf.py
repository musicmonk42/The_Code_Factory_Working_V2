# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Sphinx configuration for The Code Factory platform documentation.

Build the HTML docs locally with::

    sphinx-build -b html docs docs/_build/html

Or via the project Makefile::

    make docs

The CI workflow (.github/workflows/docs.yml) runs the same command and
uploads the output as a build artifact.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — make the project root importable for autodoc
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------

project = "The Code Factory"
author = "Novatrax Labs"
copyright = f"2025–{datetime.now().year}, Novatrax Labs LLC"  # noqa: A001
release = "1.0.0"
version = ".".join(release.split(".")[:2])  # "1.0"

# ---------------------------------------------------------------------------
# General configuration
# ---------------------------------------------------------------------------

extensions = [
    # Core autodoc support
    "sphinx.ext.autodoc",
    "sphinx.ext.autodoc.typehints",
    # Google / NumPy docstring support
    "sphinx.ext.napoleon",
    # Cross-reference Python objects in the stdlib
    "sphinx.ext.intersphinx",
    # "View source" links on every page
    "sphinx.ext.viewcode",
    # Inline TODO notes (shown only when SPHINX_INCLUDE_TODOS=1)
    "sphinx.ext.todo",
    # Markdown support via MyST
    "myst_parser",
]

# Accept both reStructuredText and Markdown source files
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# The root document (no extension)
root_doc = "index"

templates_path = ["_templates"]

# Patterns to exclude from the source tree
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "**/__pycache__",
    "**/*.pyc",
]

# Suppress known-noisy warning categories that originate from pre-existing
# content in the docs/*.md files.  Add new categories here rather than
# silencing the entire build with --no-warning-is-error.
suppress_warnings = [
    # Relative links in existing .md files that point outside the docs tree
    "myst.xref_missing",
    # Documents that exist but are not yet wired into a toctree
    "toc.not_readable",
]

# ---------------------------------------------------------------------------
# MyST parser options
# ---------------------------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",    # ::: fenced directives (GitHub-flavored Markdown compat)
    "deflist",        # definition lists
    "fieldlist",      # field lists (key: value pairs)
    "tasklist",       # - [ ] / - [x] task lists
    "smartquotes",    # curly quotes
    "strikethrough",  # ~~deleted text~~
]

myst_heading_anchors = 3  # Auto-anchor headings up to H3

# ---------------------------------------------------------------------------
# Autodoc options
# ---------------------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "private-members": False,
    "special-members": "__init__",
    "inherited-members": False,
    "show-inheritance": True,
}

# Keep original source order rather than alphabetical
autodoc_member_order = "bysource"

# Show type hints in the description, not the signature
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"

# ---------------------------------------------------------------------------
# Napoleon (Google-style docstrings)
# ---------------------------------------------------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = True

# ---------------------------------------------------------------------------
# Intersphinx — cross-link to upstream library docs
# ---------------------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "fastapi": ("https://fastapi.tiangolo.com", None),
    "pydantic": ("https://docs.pydantic.dev/latest", None),
}

# Allow intersphinx to proceed even if a remote inventory is unreachable
intersphinx_timeout = 10

# ---------------------------------------------------------------------------
# TODO extension
# ---------------------------------------------------------------------------

todo_include_todos = os.environ.get("SPHINX_INCLUDE_TODOS", "0") == "1"

# ---------------------------------------------------------------------------
# HTML output — sphinx-rtd-theme
# ---------------------------------------------------------------------------

html_theme = "sphinx_rtd_theme"

# Options supported by sphinx-rtd-theme 2.x / 3.x
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "includehidden": True,
    "titles_only": False,
    "prev_next_buttons_location": "bottom",
    "style_external_links": True,
}

html_static_path = ["_static"]
html_title = f"{project} {release}"
html_short_title = project

# Show the "last updated on" date
html_last_updated_fmt = "%Y-%m-%d"

# Do not bundle source files in the HTML output (reduces image size)
html_copy_source = False
html_show_sourcelink = False

