"""Tests for deploy/vhost.conf validation (depp/vhost.py)."""

import pytest

from depp.vhost import VhostSnippetError, validate_vhost_snippet

GOOD = """\
<LocationMatch "^/manage(/|$)">
    Require ip 192.0.2.0/24 2001:db8::/32
</LocationMatch>
Protocols http/1.1
ProxyPass /ws ${DEPP_BACKEND}/ws upgrade=websocket
"""


def test_plain_snippet_is_accepted(tmp_path):
    snippet = tmp_path / "vhost.conf"
    snippet.write_text(GOOD)

    validate_vhost_snippet(snippet)


def test_comments_mentioning_a_vhost_are_fine(tmp_path):
    """Apache ignores comment lines, so the validator must too."""
    snippet = tmp_path / "vhost.conf"
    snippet.write_text("# spliced into <VirtualHost *:443> by depp\n" + GOOD)

    validate_vhost_snippet(snippet)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("", "empty"),
        ("   \n\n", "empty"),
        ("<VirtualHost *:443>\n</VirtualHost>\n", "VirtualHost"),
        ("Protocols h2\n</virtualhost>\n", "VirtualHost"),
        ("< MDomainSet x>\n", "MDomainSet"),
        ("Header set X-Test ok\0\n", "NUL"),
    ],
)
def test_bad_snippets_are_rejected(tmp_path, contents, message):
    snippet = tmp_path / "vhost.conf"
    snippet.write_text(contents)

    with pytest.raises(VhostSnippetError, match=message):
        validate_vhost_snippet(snippet)


def test_non_utf8_snippet_is_rejected(tmp_path):
    snippet = tmp_path / "vhost.conf"
    snippet.write_bytes(b"Header set X-Test \xff\n")

    with pytest.raises(VhostSnippetError, match="UTF-8"):
        validate_vhost_snippet(snippet)


def test_directory_and_missing_file_are_rejected(tmp_path):
    with pytest.raises(VhostSnippetError, match="regular file"):
        validate_vhost_snippet(tmp_path)
    with pytest.raises(VhostSnippetError, match="regular file"):
        validate_vhost_snippet(tmp_path / "missing.conf")
