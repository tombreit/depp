"""Shared SSH option handling."""

HOST_KEY_POLICIES = ("strict", "accept-new", "insecure")


def host_key_options(policy: str) -> list[str]:
    """Return OpenSSH arguments for a host-key verification policy."""
    if policy == "strict":
        return ["-o", "StrictHostKeyChecking=yes"]
    if policy == "accept-new":
        return ["-o", "StrictHostKeyChecking=accept-new"]
    if policy == "insecure":
        return [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
        ]
    raise ValueError(f"Unknown SSH host-key policy: {policy}")
