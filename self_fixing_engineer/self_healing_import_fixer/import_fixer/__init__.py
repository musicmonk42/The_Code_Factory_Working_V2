# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# inside import_fixer/__init__.py
import sys as _sys

from . import fixer_ast as _fixer_ast
from . import fixer_dep as _fixer_dep
from . import fixer_plugins as _fixer_plugins
from . import fixer_validate as _fixer_validate

# Provide bare-name aliases for legacy/bare imports
_sys.modules.setdefault("fixer_dep", _fixer_dep)
_sys.modules.setdefault("fixer_ast", _fixer_ast)
_sys.modules.setdefault("fixer_plugins", _fixer_plugins)
_sys.modules.setdefault("fixer_validate", _fixer_validate)
