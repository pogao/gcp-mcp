from typing import Any

import pytest

from gcp_mcp.insights import owners, public_bindings, ssh_open_to_world


def rule(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "allow-ssh",
        "network": "https://www.googleapis.com/compute/v1/projects/example-project/global/networks/default",
        "direction": "INGRESS",
        "disabled": False,
        "sourceRanges": ["0.0.0.0/0"],
        "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
    }
    return {**base, **changes}


@pytest.mark.parametrize(
    ("firewall", "open_to_world"),
    [
        (rule(), True),
        (rule(disabled=True), False),
        (rule(direction="EGRESS"), False),
        (rule(allowed=[{"IPProtocol": "tcp", "ports": ["20-30"]}]), True),
        (rule(allowed=[{"IPProtocol": "all"}]), True),
        (rule(allowed=[{"IPProtocol": "tcp"}]), True),
        (rule(allowed=[{"IPProtocol": "6", "ports": ["22"]}]), True),
        (rule(sourceRanges=["::/0"]), True),
        (rule(sourceRanges=["10.0.0.0/8", "192.168.0.0/16"]), False),
        (rule(allowed=[{"IPProtocol": "udp", "ports": ["22"]}]), False),
        (rule(allowed=[{"IPProtocol": "tcp", "ports": ["80", "443"]}]), False),
        (rule(allowed=[{"IPProtocol": "tcp", "ports": ["23-30"]}]), False),
        (rule(allowed=[], denied=[{"IPProtocol": "tcp", "ports": ["22"]}]), False),
    ],
    ids=[
        "tcp 22 from anywhere",
        "disabled",
        "egress",
        "range covering 22",
        "all protocols and ports",
        "tcp on every port",
        "protocol number 6",
        "ipv6 anywhere",
        "private ranges only",
        "udp 22",
        "web ports only",
        "range missing 22",
        "deny rule",
    ],
)
def test_ssh_open_to_world(firewall: dict[str, Any], open_to_world: bool) -> None:
    assert ssh_open_to_world(firewall) is open_to_world


def test_owners_flattens_the_members_of_every_owner_binding() -> None:
    policy = {
        "bindings": [
            {"role": "roles/viewer", "members": ["user:viewer@example.com"]},
            {"role": "roles/owner", "members": ["user:a@example.com", "group:ops@example.com"]},
            {
                "role": "roles/owner",
                "members": ["serviceAccount:sa@example-project.iam.gserviceaccount.com"],
                "condition": {"expression": "request.time < timestamp('2027-01-01T00:00:00Z')"},
            },
        ]
    }
    assert owners(policy) == [
        "user:a@example.com",
        "group:ops@example.com",
        "serviceAccount:sa@example-project.iam.gserviceaccount.com",
    ]


def test_owners_of_a_policy_without_owner_bindings_is_empty() -> None:
    assert owners({"bindings": [{"role": "roles/viewer", "members": ["user:v@example.com"]}]}) == []
    assert owners({"etag": "BwX", "version": 3}) == []


def test_public_bindings_name_the_role_and_the_public_member() -> None:
    policy = {
        "bindings": [
            {"role": "roles/storage.admin", "members": ["user:admin@example.com"]},
            {
                "role": "roles/storage.objectViewer",
                "members": ["allUsers", "user:reader@example.com"],
            },
            {"role": "roles/storage.legacyBucketReader", "members": ["allAuthenticatedUsers"]},
        ]
    }
    assert public_bindings(policy) == [
        {"role": "roles/storage.objectViewer", "member": "allUsers"},
        {"role": "roles/storage.legacyBucketReader", "member": "allAuthenticatedUsers"},
    ]


def test_public_bindings_of_a_private_policy_is_empty() -> None:
    policy = {"bindings": [{"role": "roles/storage.admin", "members": ["user:admin@example.com"]}]}
    assert public_bindings(policy) == []
