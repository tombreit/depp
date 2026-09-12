"""depp — DEployment and Provisioning Python wrapper.

Copyright (c) 2026 Thomas Breitner
Licensed under the EUPL-1.2-or-later. See the LICENSE file.
"""

from depp.cli import main

# The version is not defined here: setuptools-scm derives it from the git tag at
# build time and records it in the package metadata, which the CLI's --version
# flag reads back via `importlib.metadata`. See `_read_version` in cli.py.

__all__ = ["main"]
