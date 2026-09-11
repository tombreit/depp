"""Tests for the generated Apache vhost (apache-vhost.conf.j2).

These guard directives whose absence fails silently and only shows up in
production: a missing MDMembers pin breaks certificate renewal, a missing
X-Forwarded-Host reset breaks any app behind a second reverse proxy, and a
host-hardcoded redirect bounces visitors off a ServerAlias.
"""

from tests.template_render import (
    VHOST_TEMPLATE,
    VHOST_TEMPLATE_DIR,
    render_template,
)

BASE_VARS = {
    "inventory_hostname": "app.example.com",
    "app_name": "example",
    "deploy_user_name": "app.example.com",
    "host_loopback_port": 8100,
    "apache_extra_conf_path": "/etc/apache2/depp/app.example.com.vhost.conf",
    "acme_certificate_authority": "https://acme.example/directory",
    "acme_contact_email": "ops@example.com",
    "acme_external_account_binding": None,
}


def render(**overrides):
    return render_template(
        VHOST_TEMPLATE_DIR, VHOST_TEMPLATE, {**BASE_VARS, **overrides}
    )


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


def test_loopback_port_is_templated_into_the_proxy():
    conf = render(host_loopback_port=9123)
    assert 'ProxyPass / "http://127.0.0.1:9123/"' in directives(conf, "ProxyPass")


def test_external_account_binding_is_optional():
    assert directives(render(), "MDExternalAccountBinding") == []
    conf = render(acme_external_account_binding="kid hmac")
    assert directives(conf, "MDExternalAccountBinding") == [
        "MDExternalAccountBinding kid hmac"
    ]


def vhost_443(conf):
    """The text of the :443 vhost only."""
    start = conf.index("<VirtualHost *:443>")
    return conf[start : conf.index("</VirtualHost>", start)]


def test_no_include_without_snippet():
    assert directives(render(), "Include") == []


def test_include_is_emitted_only_inside_the_https_vhost():
    conf = render(vhost_extra_src="/project/deploy/vhost.conf")
    assert directives(conf, "Include") == [
        "Include /etc/apache2/depp/app.example.com.vhost.conf"
    ]
    assert "Include " in vhost_443(conf)
    http_vhost = conf[
        conf.index("<VirtualHost *:80>") : conf.index("<VirtualHost *:443>")
    ]
    assert "Include " not in http_vhost


def test_include_follows_defaults_and_precedes_catch_all_proxypass():
    """A restated directive in the snippet must win (last wins), and a project
    ProxyPass must be matched before depp's catch-all (first wins)."""
    body = vhost_443(render(vhost_extra_src="/project/deploy/vhost.conf"))
    include = body.index("Include ")
    assert include > body.index("RequestHeader unset X-Forwarded-Host")
    assert include > body.index("LogLevel ")
    assert include > body.index("Protocols ")
    assert include < body.index('ProxyPass / "')


def test_maintenance_exclusion_precedes_catch_all_proxypass():
    body = vhost_443(render())
    assert body.index("ProxyPass /__depp_maintenance.html !") < body.index(
        'ProxyPass / "'
    )


def test_defines_expose_backend_and_identity():
    conf = render()
    assert directives(conf, "Define ") == [
        "Define DEPP_FQDN app.example.com",
        "Define DEPP_APP example",
        "Define DEPP_USER app.example.com",
        "Define DEPP_LOOPBACK_PORT 8100",
        "Define DEPP_BACKEND http://127.0.0.1:8100",
    ]
    assert sorted(directives(conf, "UnDefine ")) == sorted(
        f"UnDefine {name}"
        for name in (
            "DEPP_FQDN",
            "DEPP_APP",
            "DEPP_USER",
            "DEPP_LOOPBACK_PORT",
            "DEPP_BACKEND",
        )
    )
    # Defined before any vhost and undefined after the last one, so they do
    # not leak into hand-written vhosts parsed later.
    assert conf.index("Define DEPP_FQDN") < conf.index("<MDomainSet")
    assert conf.index("UnDefine DEPP_FQDN") > conf.rindex("</VirtualHost>")
