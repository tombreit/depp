"""depp — DEployment and Provisioning Python wrapper.

Copyright (c) 2026 Thomas Breitner
Licensed under the EUPL-1.2-or-later. See the LICENSE file.
"""

from depp.cli import main

# Single source of truth for the version: pyproject.toml reads it from here via
# setuptools' dynamic `attr:` directive, and the CLI's --version flag reads the
# resulting package metadata.
__version__ = "0.0.1"

__all__ = ["main", "__version__"]
