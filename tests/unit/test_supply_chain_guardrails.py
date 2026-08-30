# -*- coding: utf-8 -*-
"""Adversarial tests for supply-chain node guardrails.

Key invariant (project convention): TaskRequest.action MUST be snake_case
(e.g. ``supply_chain_check_inventory``), NOT dotted (``supply_chain.check_inventory``).
A dotted action is not in VALID_ACTIONS and must be rejected, so a misrouted/malformed
request can never trigger a node. Also covers SKU format + read-only enforcement.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from uuid import uuid4

import pytest
from pydantic import ValidationError

from agents.supply_chain.inventory_guardrails import InventoryGuardrails
from packages.contracts.models import TaskRequest, TaskContext
from packages.contracts.enums import Domain


ORG = "00000000-0000-0000-0000-000000000001"


def _req(action: str, payload: dict | None = None) -> TaskRequest:
    return TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action=action,
        payload=payload or {},
        context=TaskContext(organization_id=uuid4(), channel="telegram"),
    )


@pytest.fixture
def g() -> InventoryGuardrails:
    return InventoryGuardrails()


# --- action format enforcement (snake_case, never dotted) ---------------------

def test_valid_snake_case_action_passes(g):
    g.validate_input(_req("supply_chain_check_inventory"))


def test_dotted_action_rejected(g):
    # Dotted action names are rejected — either by the guardrail (PermissionError)
    # or earlier by the TaskRequest Pydantic validator (ValidationError). Both are
    # correct: a dotted action must NEVER reach a node.
    with pytest.raises((PermissionError, ValidationError)):
        g.validate_input(_req("supply_chain.check_inventory"))


def test_unknown_snake_action_rejected(g):
    with pytest.raises(PermissionError):
        g.validate_input(_req("supply_chain_launch_nukes"))


def test_write_action_rejected(g):
    with pytest.raises(PermissionError):
        g.validate_input(_req("supply_chain_update_inventory"))


# --- SKU format validation ----------------------------------------------------

def test_valid_sku_passes(g):
    g.validate_input(_req("supply_chain_check_inventory", {"items": [{"sku": "ABC-123", "quantity": 10}]}))


def test_invalid_sku_rejected(g):
    with pytest.raises(ValueError):
        g.validate_input(_req("supply_chain_check_inventory", {"items": [{"sku": "ab", "quantity": 10}]}))


def test_negative_quantity_rejected(g):
    with pytest.raises(ValueError):
        g.validate_input(_req("supply_chain_check_inventory", {"items": [{"sku": "ABC-123", "quantity_on_hand": -5}]}))


def test_non_list_items_rejected(g):
    with pytest.raises(ValueError):
        g.validate_input(_req("supply_chain_check_inventory", {"items": "not-a-list"}))


# --- valid actions are all snake_case (regression guard) ----------------------

def test_all_valid_actions_snake_case(g):
    import re

    for a in g.VALID_ACTIONS:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", a), f"action {a!r} is not snake_case"
        assert "." not in a, f"action {a!r} must not be dotted"
