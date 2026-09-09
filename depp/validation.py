"""Validation helpers shared across the manifest and inventory modules.

Both the developer-authored Pod manifest and depp.toml carry names that must
be valid lowercase DNS names, and both feed strings into files generated on a
remote host. The checks live here so the two callers cannot drift apart, while
each still raises its own error type and wording.
"""

from __future__ import annotations

import re

# A single DNS label: lowercase alphanumerics and hyphens, with no leading or
# trailing hyphen (RFC 1123, as used for Kubernetes object names).
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")

MAX_DNS_LABEL_LENGTH = 63
MAX_DNS_SUBDOMAIN_LENGTH = 253


def is_valid_dns_name(
    value: object,
    *,
    max_length: int,
    allow_dots: bool = True,
) -> bool:
    """Return whether ``value`` is a lowercase RFC 1123 DNS name.

    With ``allow_dots`` the value is validated as a dotted subdomain and every
    label must pass on its own; without it the whole value must be one label.
    """
    if not isinstance(value, str) or len(value) > max_length:
        return False
    labels = value.split(".") if allow_dots else [value]
    return all(
        label and len(label) <= MAX_DNS_LABEL_LENGTH and DNS_LABEL.fullmatch(label)
        for label in labels
    )


def has_control_characters(value: str) -> bool:
    """Return whether ``value`` contains control characters.

    Config values reach the remote host by being templated into line-oriented
    files such as the Apache virtual host. A newline in one of them would let
    the value inject unrelated directives into a root-owned config, so strings
    that land in generated config are rejected outright when they carry one.
    """
    return any(char.isspace() and char != " " or ord(char) < 32 for char in value)
