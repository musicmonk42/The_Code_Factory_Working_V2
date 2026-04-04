"""Fallback content generators extracted from ``omnicore_service.py``.

Generates README files and frontend scaffold files when the primary
generation agents fail or time out.  All functions are pure (no service
state) and operate only on the filesystem and provided arguments.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Optional, Set

from server.services.helpers._templates import (
    APP_JS_TEMPLATE,
    JINJA2_README_TEMPLATE,
    PLAIN_README_TEMPLATE,
    STYLE_CSS_TEMPLATE,
)

logger = logging.getLogger(__name__)

try:
    from jinja2 import Template
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False
    Template = None  # type: ignore[assignment,misc]

# Constants for README generation
MAX_FILES_IN_README = 10
MAX_DEPENDENCIES_IN_README = 5


def _load_readme_from_disk(job_dir: Path) -> Optional[str]:
    """Load README content from a job directory.

    Args:
        job_dir: Path to the job directory.

    Returns:
        README content as string, or ``None`` if not found.
    """
    if not job_dir.exists():
        return None

    readme_patterns = ["README.md", "readme.md", "README.txt", "readme.txt"]

    for pattern in readme_patterns:
        readme_path = job_dir / pattern
        if readme_path.exists() and readme_path.is_file():
            try:
                return readme_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Error reading {readme_path}: {e}")
                continue

    try:
        for f in job_dir.glob("*.md"):
            if f.is_file():
                return f.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error scanning for .md files in {job_dir}: {e}")

    return None


# NOTE: _generate_fallback_readme exceeds the 40-line guideline due to
# metadata scanning logic.  Preserved as-is for behavioural equivalence.
def _generate_fallback_readme(
    project_name: str = "generated_project",
    language: str = "python",
    output_path: Optional[str] = None,
) -> str:
    """Generate a deterministic fallback README when DocGen fails.

    Args:
        project_name: Name of the generated project.
        language: Programming language of the project.
        output_path: Path to the generated project (for scanning).

    Returns:
        Complete README content as a string.
    """
    endpoints: list = []
    dependencies: list = []
    file_list: list = []

    if output_path:
        output_path_obj = Path(output_path)
        if output_path_obj.exists():
            py_files = list(output_path_obj.rglob("*.py"))
            file_list = [
                str(f.relative_to(output_path_obj))
                for f in py_files[:MAX_FILES_IN_README]
            ]
            for main_file in [
                output_path_obj / "main.py",
                output_path_obj / "app" / "main.py",
            ]:
                if main_file.exists():
                    try:
                        content = main_file.read_text(encoding="utf-8")
                        pats = [
                            r'@app\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                            r'@router\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                        ]
                        for pat in pats:
                            for method, path in re.findall(pat, content):
                                endpoints.append(f"{method.upper()} {path}")
                    except Exception as e:
                        logger.debug(f"Could not extract endpoints: {e}")

            req_file = output_path_obj / "requirements.txt"
            if req_file.exists():
                try:
                    for line in req_file.read_text(encoding="utf-8").split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            pkg = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
                            if pkg:
                                dependencies.append(pkg)
                except Exception as e:
                    logger.debug(f"Could not read requirements.txt: {e}")

    if JINJA2_AVAILABLE and Template:
        template = Template(JINJA2_README_TEMPLATE)
        return template.render(
            project_name=project_name,
            language=language,
            endpoints=endpoints,
            dependencies=dependencies[:MAX_DEPENDENCIES_IN_README],
            file_list=file_list,
        )

    return PLAIN_README_TEMPLATE.format(
        project_name=project_name,
        language=language,
    )


# NOTE: _generate_fallback_frontend_files exceeds the 40-line guideline
# due to directory-resolution logic.  Preserved as-is for behavioural
# equivalence.
def _generate_fallback_frontend_files(
    output_path: str,
    missing_files: Set[str],
    project_name: str = "Generated App",
) -> Dict[str, bool]:
    """Generate fallback frontend files when codegen did not produce them.

    Args:
        output_path: Path to the generated project directory.
        missing_files: Set of missing frontend file names.
        project_name: Name of the project for template content.

    Returns:
        Dictionary mapping file names to success/failure status.
    """
    results: Dict[str, bool] = {}
    output_dir = Path(output_path)

    INDEX_HTML_TEMPLATE = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '    <meta charset="UTF-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'    <title>{project_name}</title>\n'
        '    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">\n'
        '    <link rel="stylesheet" href="style.css">\n'
        '</head>\n<body>\n'
        '    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">\n'
        '        <div class="container">\n'
        f'            <a class="navbar-brand" href="#">{project_name}</a>\n'
        '        </div>\n    </nav>\n\n'
        '    <main class="container mt-4">\n'
        f'        <h1>Welcome to {project_name}</h1>\n'
        '        <p class="lead">This frontend was auto-generated to meet spec requirements.</p>\n\n'
        '        <div id="app-content">\n'
        '            <!-- Dynamic content will be loaded here -->\n'
        '        </div>\n    </main>\n\n'
        '    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>\n'
        '    <script src="app.js"></script>\n'
        '</body>\n</html>'
    )

    TEMPLATES = {
        "index.html": INDEX_HTML_TEMPLATE,
        "style.css": STYLE_CSS_TEMPLATE,
        "app.js": APP_JS_TEMPLATE,
    }

    possible_dirs = [
        output_dir / "templates", output_dir / "static",
        output_dir / "public", output_dir / "app" / "templates",
        output_dir / "app" / "static", output_dir,
    ]

    target_dir = output_dir
    for pdir in possible_dirs:
        if pdir.exists():
            target_dir = pdir
            break

    html_target = target_dir
    css_js_target = target_dir
    templates_dir = output_dir / "templates"
    static_dir = output_dir / "static"

    if templates_dir.exists():
        html_target = templates_dir
    elif "index.html" in missing_files:
        templates_dir.mkdir(parents=True, exist_ok=True)
        html_target = templates_dir

    if static_dir.exists():
        css_js_target = static_dir
    elif "style.css" in missing_files or "app.js" in missing_files:
        static_dir.mkdir(parents=True, exist_ok=True)
        css_js_target = static_dir

    for filename in missing_files:
        if filename not in TEMPLATES:
            results[filename] = False
            continue
        try:
            if filename == "index.html":
                file_path = html_target / filename
            else:
                file_path = css_js_target / filename
            file_path.write_text(TEMPLATES[filename], encoding="utf-8")
            results[filename] = True
            logger.info(f"Generated fallback frontend file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to generate fallback {filename}: {e}")
            results[filename] = False

    return results
