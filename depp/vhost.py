"""Validation of the optional per-project Apache fragment (deploy/vhost.conf).

The file is copied verbatim to the host and ``Include``d inside depp's
``<VirtualHost *:443>``, so Apache itself validates the directives (the
provision playbook runs ``apachectl configtest`` before reloading). What is
checked here is only what Apache would accept but depp cannot mean: a
fragment that opens its own vhost or managed domain would silently sit
outside the block it was meant to extend.
"""

import re
from pathlib import Path

# Case-insensitive, like Apache's own parser.
FORBIDDEN_SECTIONS = re.compile(r"<\s*/?\s*(VirtualHost|MDomainSet)\b", re.IGNORECASE)


class VhostSnippetError(ValueError):
    """The vhost snippet cannot be installed as is."""


def validate_vhost_snippet(path: Path) -> None:
    """Raise VhostSnippetError unless ``path`` is a usable vhost fragment."""
    if not path.is_file():
        raise VhostSnippetError(f"{path} is not a regular file")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise VhostSnippetError(f"{path} is not valid UTF-8") from error
    if "\0" in text:
        raise VhostSnippetError(f"{path} contains a NUL byte")
    if not text.strip():
        raise VhostSnippetError(
            f"{path} is empty; delete the file if the project has no directives"
        )
    # Apache treats a line whose first non-blank character is # as a comment,
    # and a comment that mentions <VirtualHost> is a natural thing to write.
    directives = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    match = FORBIDDEN_SECTIONS.search(directives)
    if match:
        raise VhostSnippetError(
            f"{path} contains <{match.group(1)}>: the snippet is spliced inside "
            "depp's <VirtualHost *:443> and must not open or close one itself"
        )
