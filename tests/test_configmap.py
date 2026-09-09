"""Tests for the pure-Python ConfigMap rendering (depp/configmap.py)."""

import pytest
import yaml

from depp.configmap import parse_env_file, render_configmap


def write_env(tmp_path, content):
    env_file = tmp_path / ".env"
    env_file.write_text(content)
    return env_file


def test_parse_basic_key_value(tmp_path):
    env_file = write_env(tmp_path, "KEY=value\nOTHER=thing\n")
    assert parse_env_file(env_file) == {"KEY": "value", "OTHER": "thing"}


def test_parse_skips_comments_and_blank_lines(tmp_path):
    env_file = write_env(tmp_path, "# comment\n\nKEY=value\n   \n# another\n")
    assert parse_env_file(env_file) == {"KEY": "value"}


def test_parse_rejects_lines_without_equals(tmp_path):
    env_file = write_env(tmp_path, "NOEQUALS\nKEY=value\n")

    with pytest.raises(ValueError, match=r"\.env:1: expected a KEY=value"):
        parse_env_file(env_file)


def test_parse_keeps_equals_in_value(tmp_path):
    env_file = write_env(tmp_path, "DATABASE_URL=postgres://u:p@h/db?a=b\n")
    assert parse_env_file(env_file) == {"DATABASE_URL": "postgres://u:p@h/db?a=b"}


def test_parse_strips_matching_quotes(tmp_path):
    env_file = write_env(tmp_path, "A='single'\nB=\"double\"\n")
    assert parse_env_file(env_file) == {
        "A": "single",
        "B": "double",
    }


def test_parse_strips_whitespace_around_key_and_value(tmp_path):
    env_file = write_env(tmp_path, "  KEY  =  value  \n")
    assert parse_env_file(env_file) == {"KEY": "value"}


def test_parse_rejects_empty_key(tmp_path):
    env_file = write_env(tmp_path, "=value\nKEY=v\n")

    with pytest.raises(ValueError, match=r"\.env:1: variable name is empty"):
        parse_env_file(env_file)


@pytest.mark.parametrize("key", ["HAS SPACE", "BAD@KEY", "SLASH/KEY"])
def test_parse_rejects_invalid_configmap_key(tmp_path, key):
    env_file = write_env(tmp_path, f"{key}=value\n")

    with pytest.raises(ValueError, match="invalid ConfigMap key"):
        parse_env_file(env_file)


def test_parse_rejects_duplicate_key(tmp_path):
    env_file = write_env(tmp_path, "KEY=first\nKEY=second\n")

    with pytest.raises(ValueError, match=r"\.env:2: duplicate variable 'KEY'"):
        parse_env_file(env_file)


@pytest.mark.parametrize("value", ["'unmatched", 'unmatched"'])
def test_parse_rejects_unmatched_quotes(tmp_path, value):
    env_file = write_env(tmp_path, f"KEY={value}\n")

    with pytest.raises(ValueError, match="value has unmatched quotes"):
        parse_env_file(env_file)


def test_parse_rejects_empty_file(tmp_path):
    env_file = write_env(tmp_path, "# only a comment\n")

    with pytest.raises(ValueError, match="does not define any variables"):
        parse_env_file(env_file)


def test_parse_rejects_non_utf8_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"KEY=\xff\n")

    with pytest.raises(ValueError, match="not valid UTF-8"):
        parse_env_file(env_file)


def test_render_configmap_shape(tmp_path):
    env_file = write_env(tmp_path, "SECRET_KEY=abc\nDEBUG=false\n")
    rendered = render_configmap("myapp-config", env_file)
    doc = yaml.safe_load(rendered)
    assert doc == {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "myapp-config"},
        "data": {"SECRET_KEY": "abc", "DEBUG": "false"},
    }


def test_render_preserves_key_order(tmp_path):
    env_file = write_env(tmp_path, "Z=1\nA=2\nM=3\n")
    rendered = render_configmap("cfg", env_file)
    doc = yaml.safe_load(rendered)
    assert list(doc["data"].keys()) == ["Z", "A", "M"]
