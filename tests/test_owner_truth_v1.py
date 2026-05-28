from __future__ import annotations

from completion_first_data.owner_truth import (
    SCHEMA_VERSION,
    evaluate_owner_truth_readiness,
    make_owner_source_row,
    normalize_market_fee_params,
    normalize_owner_fill_event,
    normalize_owner_inventory_event,
    normalize_owner_order_event,
    normalize_owner_redeem_settlement_event,
    owner_truth_scoring_interface,
)


def test_source_hash_is_stable_and_provenance_is_explicit() -> None:
    raw = {"id": "fill-1", "price": "0.51", "size": "10"}

    a = make_owner_source_row(
        raw_payload=raw,
        source_kind="authenticated_user_fill",
        source_channel="clob_user_ws",
        owner_account="0xrunner",
        capture_ts_ms=1777174100000,
    )
    b = make_owner_source_row(
        raw_payload={"size": "10", "price": "0.51", "id": "fill-1"},
        source_kind="authenticated_user_fill",
        source_channel="clob_user_ws",
        owner_account="0xrunner",
        capture_ts_ms=1777174109999,
    )

    assert a.source_row_id == "fill-1"
    assert a.source_hash == b.source_hash
    assert a.source_hash_algo == "sha256_canonical_json_v1"
    assert "0xrunner" in a.raw_payload_json or a.owner_account == "0xrunner"


def test_normalize_owner_fill_uses_actual_fee_when_present() -> None:
    source = make_owner_source_row(
        raw_payload={"id": "fill-1"},
        source_kind="authenticated_user_fill",
        source_channel="clob_user_ws",
        owner_account="0xrunner",
        capture_ts_ms=1777174100000,
    )

    row = normalize_owner_fill_event(
        {
            "id": "fill-1",
            "market": "0xcond",
            "asset_id": "asset-yes",
            "outcome": "YES",
            "side": "BUY",
            "maker_taker": "maker",
            "price": "0.51",
            "size": "10",
            "fee_amount": "0.001",
            "timestamp": 1777174100,
        },
        source,
    )

    assert row["schema_version"] == SCHEMA_VERSION
    assert row["record_kind"] == "owner_fill_events"
    assert row["source_row_hash"] == source.source_hash
    assert row["condition_id"] == "0xcond"
    assert row["asset_id"] == "asset-yes"
    assert row["maker_taker"] == "MAKER"
    assert row["notional"] == 5.1
    assert row["fee_amount"] == 0.001


def test_normalize_fill_can_attach_recomputable_market_fee_params() -> None:
    fee_source = make_owner_source_row(
        raw_payload={"condition_id": "0xcond", "maker_fee_bps": "0", "taker_fee_bps": "10"},
        source_kind="market_fee_params",
        source_channel="clob_market_api",
        owner_account="0xrunner",
        capture_ts_ms=1777174100000,
    )
    fee_params = normalize_market_fee_params(
        {"condition_id": "0xcond", "maker_fee_bps": "0", "taker_fee_bps": "10"},
        fee_source,
    )
    fill_source = make_owner_source_row(
        raw_payload={"id": "fill-2"},
        source_kind="authenticated_user_fill",
        source_channel="clob_user_ws",
        owner_account="0xrunner",
        capture_ts_ms=1777174100001,
    )

    row = normalize_owner_fill_event(
        {
            "id": "fill-2",
            "market": "0xcond",
            "asset_id": "asset-no",
            "outcome": "NO",
            "side": "SELL",
            "maker_taker": "taker",
            "price": "0.49",
            "size": "5",
        },
        fill_source,
        fee_params_by_condition={"0xcond": fee_params},
    )

    assert row["fee_rate_bps"] == 10.0
    assert row["market_fee_params_hash"] == fee_params["raw_params_hash"]


def test_private_truth_ready_requires_future_owner_rows_and_reconciliation() -> None:
    source = make_owner_source_row(
        raw_payload={"id": "order-1"},
        source_kind="authenticated_owner_order",
        source_channel="clob_orders_api",
        owner_account="0xrunner",
        capture_ts_ms=1777174100000,
    )
    order = normalize_owner_order_event(
        {
            "id": "order-1",
            "market": "0xcond",
            "asset_id": "asset-yes",
            "type": "placed",
            "side": "BUY",
            "price": "0.5",
            "size": "10",
        },
        source,
    )
    fill = normalize_owner_fill_event(
        {
            "id": "fill-1",
            "market": "0xcond",
            "asset_id": "asset-yes",
            "maker_taker": "maker",
            "price": "0.5",
            "size": "10",
            "fee_amount": "0",
        },
        source,
    )
    inventory = normalize_owner_inventory_event(
        {
            "condition_id": "0xcond",
            "asset_id": "asset-yes",
            "outcome": "YES",
            "position_size": "10",
            "source_kind": "reconcile",
        },
        source,
    )
    redeem = normalize_owner_redeem_settlement_event(
        {"condition_id": "0xcond", "asset_id": "asset-yes", "event_kind": "redeem", "size": "0"},
        source,
    )

    not_ready = evaluate_owner_truth_readiness(
        order_events=[order],
        fill_events=[fill],
        inventory_events=[inventory],
        redeem_settlement_events=[redeem],
        inventory_reconciled=True,
        pnl_reconciled=False,
    )
    assert not_ready["private_truth_ready"] is False
    assert not_ready["failed_checks"] == ["pnl_reconciled"]

    ready = evaluate_owner_truth_readiness(
        order_events=[order],
        fill_events=[fill],
        inventory_events=[inventory],
        redeem_settlement_events=[redeem],
        inventory_reconciled=True,
        pnl_reconciled=True,
    )
    assert ready["private_truth_ready"] is True
    assert ready["failed_checks"] == []


def test_historical_shadow_without_owner_rows_never_ready() -> None:
    manifest = evaluate_owner_truth_readiness(
        order_events=[],
        fill_events=[],
        inventory_events=[],
        inventory_reconciled=False,
        pnl_reconciled=False,
    )

    assert manifest["private_truth_ready"] is False
    assert "has_owner_orders" in manifest["failed_checks"]
    assert "has_owner_fills" in manifest["failed_checks"]
    assert "has_owner_inventory" in manifest["failed_checks"]


def test_scoring_interface_keeps_owner_truth_out_of_search_safe_layer() -> None:
    contract = owner_truth_scoring_interface()

    assert contract["schema_version"] == SCHEMA_VERSION
    assert contract["layer"] == "private_validation_scoring"
    assert "owner_fill_events" in contract["required_record_kinds"]
    assert "historical_shadow" in contract["private_truth_ready_rule"]
