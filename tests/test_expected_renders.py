"""Expected renders of the generated systemd unit and Apache vhost.

Several apps are already deployed with depp. These snapshots pin what a
project that uses none of the newer options gets, so a template change that
would alter an existing host shows up as a reviewable diff of the expected
file rather than as a surprise on the next provision or deploy.
"""

from pathlib import Path

from tests.template_render import (
    UNIT_TEMPLATE,
    UNIT_TEMPLATE_DIR,
    VHOST_TEMPLATE,
    VHOST_TEMPLATE_DIR,
    render_template,
)

EXPECTED_DIR = Path(__file__).parent / "expected"

LEGACY_VHOST_VARS = {
    "inventory_hostname": "app.example.com",
    "caddy_host_port": 8100,
    "acme_certificate_authority": "https://acme.example/directory",
    "acme_contact_email": "ops@example.com",
    "acme_external_account_binding": None,
}

LEGACY_UNIT_VARS = {
    "app_name": "example",
    "caddy_host_port": 8100,
}


def test_legacy_vhost_matches_expected():
    rendered = render_template(VHOST_TEMPLATE_DIR, VHOST_TEMPLATE, LEGACY_VHOST_VARS)
    assert rendered == (EXPECTED_DIR / "apache-vhost.legacy.conf").read_text()


def test_legacy_unit_matches_expected():
    rendered = render_template(UNIT_TEMPLATE_DIR, UNIT_TEMPLATE, LEGACY_UNIT_VARS)
    assert rendered == (EXPECTED_DIR / "kube-pod.legacy.service").read_text()
