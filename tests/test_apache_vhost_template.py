"""Tests for the generated Apache vhost (apache-vhost.conf.j2).

These guard directives whose absence fails silently and only shows up in
production: a missing MDMembers pin breaks certificate renewal, a missing
X-Forwarded-Host reset breaks any app behind a second reverse proxy, and a
host-hardcoded redirect bounces visitors off a ServerAlias.
"""

from pathlib import Path

import jinja2

import depp

TEMPLATE_DIR = Path(depp.__file__).parent / "ansible_provisioning" / "templates"
TEMPLATE_NAME = "apache-vhost.conf.j2"

BASE_VARS = {
    "inventory_hostname": "app.example.com",
    "caddy_host_port": 8100,
    "acme_certificate_authority": "https://acme.example/directory",
    "acme_contact_email": "ops@example.com",
    "acme_external_account_binding": None,
}


def render(**overrides):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    return env.get_template(TEMPLATE_NAME).render({**BASE_VARS, **overrides})


def directives(conf, name):
    """Every occurrence of a directive, ignoring comment lines."""
    return [line.strip() for line in conf.splitlines() if line.strip().startswith(name)]


def test_no_server_alias_directive_without_aliases():
    assert directives(render(), "ServerAlias") == []


def test_alias_is_emitted_in_both_vhosts():
    conf = render(server_aliases=["public.example.org"])
    assert directives(conf, "ServerAlias") == [
        "ServerAlias public.example.org",
        "ServerAlias public.example.org",
    ]


def test_multiple_aliases_are_all_emitted():
    conf = render(server_aliases=["a.example.org", "b.example.org"])
    aliases = directives(conf, "ServerAlias")
    assert aliases.count("ServerAlias a.example.org") == 2
    assert aliases.count("ServerAlias b.example.org") == 2


def test_aliases_are_not_certificate_members():
    """mod_md defaults to MDMembers auto, which would try to get a cert for
    every ServerAlias — those names usually do not resolve to this host."""
    assert directives(render(), "MDMembers") == ["MDMembers manual"]


def test_http_redirect_preserves_requested_host():
    """Must echo %{HTTP_HOST}; hardcoding the fqdn bounces aliases away."""
    conf = render(server_aliases=["public.example.org"])
    rules = directives(conf, "RewriteRule")
    assert rules == ["RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=302,L]"]
    assert directives(conf, "Redirect ") == []


def test_acme_challenges_are_not_redirected():
    """http-01 validation must stay reachable over plain HTTP."""
    conf = render()
    assert any("acme-challenge" in cond for cond in directives(conf, "RewriteCond"))


def test_forwarded_host_is_reset_before_proxying():
    """mod_proxy merges rather than replaces, so a second proxy in front would
    otherwise produce 'host, host' and break strict host validation."""
    assert "RequestHeader unset X-Forwarded-Host" in directives(
        render(), "RequestHeader"
    )


def test_forwarded_for_is_left_alone():
    """Accumulating IPs is the correct behaviour for X-Forwarded-For."""
    assert not any(
        d.startswith("RequestHeader unset X-Forwarded-For")
        for d in directives(render(), "RequestHeader")
    )


def test_scheme_is_forwarded():
    assert 'RequestHeader set X-Forwarded-Proto "https"' in directives(
        render(), "RequestHeader"
    )


def test_host_is_preserved_to_the_backend():
    assert directives(render(), "ProxyPreserveHost") == ["ProxyPreserveHost On"]


def test_caddy_port_is_templated_into_the_proxy():
    conf = render(caddy_host_port=9123)
    assert 'ProxyPass / "http://127.0.0.1:9123/"' in directives(conf, "ProxyPass")


def test_external_account_binding_is_optional():
    assert directives(render(), "MDExternalAccountBinding") == []
    conf = render(acme_external_account_binding="kid hmac")
    assert directives(conf, "MDExternalAccountBinding") == [
        "MDExternalAccountBinding kid hmac"
    ]
