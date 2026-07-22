"""Unit tests for terragrunt/common/networks.json -- the namespace-keyed CIDR registry.

These tests assert the structural and content constraints for the CIDR allocation
registry at terragrunt/common/networks.json (spec S4.1, S5, AC-3, AC-5).

Assertions:
- The file exists at terragrunt/common/networks.json and NOT inside terragrunt/live/.
- The file parses as a valid JSON object.
- Every present row is keyed by a full derived namespace string.
- Every present row's value object carries the required vpc_cidr_block sub-key.
- Every vpc_cidr_block value is a well-formed CIDR string.
- All seeded vpc_cidr_block values are distinct (non-overlapping per spec S3.6).
"""

from __future__ import annotations

import ipaddress
import json
import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
NETWORKS_JSON_PATH = REPO_ROOT / "terragrunt" / "common" / "networks.json"

# Derived namespace pattern: product-regionclean-env-envinstance-service-serviceinstance
# Example: telemetry-useast1-sandbox-000-collector_ingestion-000
# Namespace field rule: "-" SEPARATES the 6 fields; "_" JOINS words WITHIN a field
# (multi-word service "collector-ingestion" dir -> "collector_ingestion" field). So each
# inner field may contain "_" (env/env-instance may too: dns_owner, *_role).
_NAMESPACE_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*"  # product family
    r"-[a-z][a-z0-9]*"  # region (no separators: useast1)
    r"-[a-z][a-z0-9_]*"  # environment (single word; allow "_" for safety)
    r"-[0-9]+"  # environment instance (e.g. 000)
    r"-[a-z][a-z0-9_]*"  # service ("_" joins words within the field: collector_ingestion)
    r"-[0-9]+$"  # service instance (e.g. 000)
)

# CIDR pattern: any valid IPv4 CIDR (e.g. 10.1.0.0/16). Stricter validation via
# ipaddress.IPv4Network is applied in a separate assertion.
_CIDR_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_networks() -> dict[str, object]:
    """Load and return networks.json as a dict. Fails if the file does not exist."""
    assert NETWORKS_JSON_PATH.exists(), (
        f"terragrunt/common/networks.json does not exist at {NETWORKS_JSON_PATH}. "
        "Author the file as part of E8-F1-S1-T4 (spec AC-3)."
    )
    content = NETWORKS_JSON_PATH.read_text(encoding="utf-8")
    data = json.loads(content)
    assert isinstance(data, dict), (
        f"terragrunt/common/networks.json must be a JSON object, got {type(data).__name__}."
    )
    return data


# ---------------------------------------------------------------------------
# Existence and location tests (AC-3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_networks_json_exists_outside_live() -> None:
    """networks.json must exist at terragrunt/common/ and NOT inside terragrunt/live/."""
    assert NETWORKS_JSON_PATH.exists(), (
        f"terragrunt/common/networks.json not found at {NETWORKS_JSON_PATH} (AC-3). "
        "Author the file as part of E8-F1-S1-T4."
    )
    live_path = REPO_ROOT / "terragrunt" / "live" / "common" / "networks.json"
    assert not live_path.exists(), (
        f"networks.json must NOT reside inside terragrunt/live/ (AC-3). "
        f"Found forbidden copy at {live_path}."
    )


@pytest.mark.unit
def test_networks_json_parses_as_object() -> None:
    """networks.json must parse as a valid JSON object (not array or primitive)."""
    data = _load_networks()
    assert isinstance(data, dict), (
        f"networks.json root must be a JSON object ({{...}}), got {type(data).__name__}."
    )


# ---------------------------------------------------------------------------
# Schema tests -- key and value structure (AC-3, spec S4.1, S5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_networks_json_keys_are_derived_namespaces() -> None:
    """Every top-level key in networks.json must be a full derived namespace string.

    A derived namespace matches the pattern:
      <product>-<regionclean>-<env>-<envinstance>-<service>-<serviceinstance>
    where regionclean is the AWS region with hyphens removed (e.g. useast1).
    """
    data = _load_networks()
    invalid_keys = [k for k in data if not _NAMESPACE_PATTERN.match(k)]
    assert not invalid_keys, (
        f"The following keys in networks.json are not valid derived namespaces "
        f"(spec S4.1, S5): {invalid_keys}. "
        "Each key must match the pattern: "
        "<product>-<regionclean>-<env>-<envinstance>-<service>-<serviceinstance>."
    )


@pytest.mark.unit
def test_networks_json_every_row_has_vpc_cidr_block() -> None:
    """Every row in networks.json must carry the required vpc_cidr_block sub-key."""
    data = _load_networks()
    missing = [ns for ns, v in data.items() if not isinstance(v, dict) or "vpc_cidr_block" not in v]
    assert not missing, (
        f"The following namespaces in networks.json are missing the required "
        f"vpc_cidr_block sub-key (spec S4.1, S5): {missing}."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "namespace",
    [
        "telemetry-useast1-sandbox-000-collector_ingestion-000",
        "telemetry-useast1-prod-000-collector_ingestion-000",
    ],
)
def test_networks_json_seeded_namespaces_present(namespace: str) -> None:
    """The known VPC-creating unit namespaces must be seeded in networks.json."""
    data = _load_networks()
    assert namespace in data, (
        f"Required namespace '{namespace}' is missing from networks.json. "
        "Seed the registry row for each VPC-creating unit (spec S4.1, S5, AC-3)."
    )


@pytest.mark.unit
def test_networks_json_prod_collector_ingestion_cidr_value() -> None:
    """Prod collector-ingestion namespace must have vpc_cidr_block 10.0.0.0/16."""
    data = _load_networks()
    prod_key = "telemetry-useast1-prod-000-collector_ingestion-000"
    assert prod_key in data, (
        f"Prod namespace '{prod_key}' is missing from networks.json (AC-NET-01, AC-NET-02)."
    )
    row = data[prod_key]
    assert isinstance(row, dict), (
        f"Value for '{prod_key}' must be a dict, got {type(row).__name__}."
    )
    cidr = row.get("vpc_cidr_block")
    assert cidr == "10.0.0.0/16", (
        f"Expected vpc_cidr_block '10.0.0.0/16' for prod namespace '{prod_key}', got '{cidr}' "
        "(AC-NET-02, spec S3.6, D47)."
    )


# ---------------------------------------------------------------------------
# CIDR format tests (AC-3, spec S5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_networks_json_cidr_values_are_well_formed() -> None:
    """Every vpc_cidr_block value must be a syntactically valid IPv4 CIDR string."""
    data = _load_networks()
    invalid = []
    for ns, v in data.items():
        if not isinstance(v, dict):
            continue
        cidr = v.get("vpc_cidr_block", "")
        if not _CIDR_PATTERN.match(str(cidr)):
            invalid.append((ns, cidr))
    assert not invalid, (
        f"The following networks.json rows have malformed vpc_cidr_block values "
        f"(spec S5): {invalid}. "
        "Each vpc_cidr_block must be a dotted-decimal IPv4 CIDR (e.g. 10.1.0.0/16)."
    )


@pytest.mark.unit
def test_networks_json_cidr_values_are_valid_networks() -> None:
    """Every vpc_cidr_block must be a valid IPv4 network (strict=False allows host bits)."""
    data = _load_networks()
    invalid = []
    for ns, v in data.items():
        if not isinstance(v, dict):
            continue
        cidr = v.get("vpc_cidr_block", "")
        try:
            ipaddress.IPv4Network(str(cidr), strict=False)
        except ValueError as exc:
            invalid.append((ns, cidr, str(exc)))
    assert not invalid, (
        f"The following networks.json rows have invalid vpc_cidr_block values: "
        f"{invalid}. Each value must be parseable as an IPv4 network."
    )


# ---------------------------------------------------------------------------
# Non-overlap test (AC-5, spec S3.6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_networks_json_cidr_blocks_are_distinct() -> None:
    """All seeded vpc_cidr_block values must be distinct (no exact duplicates).

    Exact CIDR equality is tested here. Strict subnet-overlap detection is
    qualitatively verified against spec S3.6: because the sandbox account
    uses 10.1.0.0/16 and the prod account uses 10.0.0.0/16, the blocks are
    non-overlapping by construction. This test guards against accidental
    copy-paste of the same CIDR across two rows.
    """
    data = _load_networks()
    cidrs = [
        v["vpc_cidr_block"] for v in data.values() if isinstance(v, dict) and "vpc_cidr_block" in v
    ]
    assert len(cidrs) == len(set(cidrs)), (
        f"Duplicate vpc_cidr_block values detected in networks.json (spec S3.6). "
        f"Every seeded CIDR must be distinct to prevent CIDR overlap. "
        f"Found: {cidrs}."
    )


@pytest.mark.unit
def test_networks_json_cidr_blocks_do_not_overlap() -> None:
    """All seeded vpc_cidr_block values must not overlap as IP networks (spec S3.6)."""
    data = _load_networks()
    networks = []
    for ns, v in data.items():
        if not isinstance(v, dict) or "vpc_cidr_block" not in v:
            continue
        try:
            net = ipaddress.IPv4Network(v["vpc_cidr_block"], strict=False)
            networks.append((ns, net))
        except ValueError:
            # Malformed CIDR -- already caught by test_networks_json_cidr_values_are_valid_networks
            continue

    overlaps = []
    for i, (ns_a, net_a) in enumerate(networks):
        for ns_b, net_b in networks[i + 1 :]:
            if net_a.overlaps(net_b):
                overlaps.append((ns_a, str(net_a), ns_b, str(net_b)))

    assert not overlaps, (
        f"Overlapping vpc_cidr_block values in networks.json (spec S3.6): {overlaps}. "
        "The registry must prevent CIDR overlap -- assign distinct, non-overlapping blocks."
    )


# ---------------------------------------------------------------------------
# No-secrets test (security rules, spec S3.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_networks_json_contains_no_secrets() -> None:
    """networks.json must contain only non-secret config (CIDR blocks, no tokens).

    This test confirms the file is free of known secret patterns: AWS access keys,
    API tokens, and password-like fields. CIDR blocks are non-secret per spec S3.5.
    """
    content = NETWORKS_JSON_PATH.read_text(encoding="utf-8") if NETWORKS_JSON_PATH.exists() else ""
    secret_patterns = [
        re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key ID
        re.compile(r"(?i)\"password\"\s*:"),  # password field
        re.compile(r"(?i)\"secret\"\s*:"),  # secret field
        re.compile(r"(?i)\"token\"\s*:"),  # token field
        re.compile(r"(?i)\"api_key\"\s*:"),  # api_key field
    ]
    found = [p.pattern for p in secret_patterns if p.search(content)]
    assert not found, (
        f"Potential secret patterns found in networks.json: {found}. "
        "The file must contain only non-secret config (spec S3.5)."
    )
