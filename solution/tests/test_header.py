"""Unit tests for CSV header normalization and schema alias mapping."""
from __future__ import annotations

import pytest

from solution.models.schema_loader import SchemaRegistry
from solution.validation.header import map_headers, normalize_header


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("customer_name", "customer name"),
        ("  Mobile_No ", "mobile no"),
        ("Insured   Name", "insured name"),
        ("POLICY__NO", "policy no"),
        ("  _extra_  col_ ", "extra col"),
    ],
)
def test_normalize_header(raw: str, expected: str) -> None:
    assert normalize_header(raw) == expected


def test_map_headers_motor_renewal_canonical_and_aliased(
    schema_registry: SchemaRegistry,
) -> None:
    mr = schema_registry.get_workflow("motor_renewal")

    # Canonical headers
    canonical_cols = [
        "customer_name",
        "phone",
        "policy_number",
        "policy_expiry",
        "premium_amount",
        "preferred_language",
    ]
    assert map_headers(canonical_cols, mr) == {col: col for col in canonical_cols}

    # Aliased, messy whitespace/underscore, reordered, and extra ignored columns
    aliased_cols = [
        "Language",
        "  Insured__Name ",
        "Policy No",
        "Mobile_No",
        "Expiry Date",
        "Premium",
        "agent_notes",
        "unknown_col",
    ]
    assert map_headers(aliased_cols, mr) == {
        "Language": "preferred_language",
        "  Insured__Name ": "customer_name",
        "Policy No": "policy_number",
        "Mobile_No": "phone",
        "Expiry Date": "policy_expiry",
        "Premium": "premium_amount",
    }


def test_map_headers_policy_feedback_aliases(schema_registry: SchemaRegistry) -> None:
    pf = schema_registry.get_workflow("policy_feedback")
    cols = ["Order ID", "Name", "Contact", "Item", "Purchase Date", "Language", "extra"]
    assert map_headers(cols, pf) == {
        "Order ID": "order_id",
        "Name": "customer_name",
        "Contact": "phone",
        "Item": "product",
        "Purchase Date": "order_date",
        "Language": "preferred_language",
    }
