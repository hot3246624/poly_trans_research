"""Future owner-side private truth contracts for runner execution.

This module deliberately stays outside the search-safe backtest path. It is a
contract/scoring layer for future controlled canary/live-small execution only.
Historical shadow/no-order dry-runs cannot be upgraded into these records.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "runner_owner_truth_v1"
SOURCE_HASH_ALGO = "sha256_canonical_json_v1"
PRIVATE_TRUTH_LAYER = "private_validation_scoring"

REQUIRED_RECORD_KINDS = {
    "owner_order_events",
    "owner_fill_events",
    "owner_inventory_events",
}


OWNER_TRUTH_TABLE_SCHEMAS: dict[str, list[str]] = {
    "owner_source_rows": [
        "source_row_id TEXT PRIMARY KEY",
        "source_kind TEXT NOT NULL",
        "source_channel TEXT NOT NULL",
        "capture_ts_ms INTEGER NOT NULL",
        "owner_account TEXT NOT NULL",
        "source_hash TEXT NOT NULL",
        "source_hash_algo TEXT NOT NULL",
        "raw_payload_json TEXT NOT NULL",
    ],
    "owner_order_events": [
        "owner_account TEXT NOT NULL",
        "source_row_id TEXT NOT NULL",
        "source_row_hash TEXT NOT NULL",
        "event_ts_ms INTEGER",
        "condition_id TEXT",
        "market_slug TEXT",
        "asset_id TEXT",
        "order_id TEXT",
        "client_order_id TEXT",
        "lifecycle_state TEXT NOT NULL",
        "market_side TEXT",
        "direction TEXT",
        "price REAL",
        "size REAL",
        "filled_size REAL",
        "remaining_size REAL",
        "maker_taker TEXT",
        "status TEXT",
        "tx_hash TEXT",
    ],
    "owner_fill_events": [
        "owner_account TEXT NOT NULL",
        "source_row_id TEXT NOT NULL",
        "source_row_hash TEXT NOT NULL",
        "event_ts_ms INTEGER",
        "condition_id TEXT NOT NULL",
        "asset_id TEXT NOT NULL",
        "order_id TEXT",
        "fill_id TEXT",
        "trade_id TEXT",
        "market_side TEXT",
        "direction TEXT",
        "maker_taker TEXT NOT NULL",
        "price REAL NOT NULL",
        "size REAL NOT NULL",
        "notional REAL",
        "fee_amount REAL",
        "fee_rate_bps REAL",
        "fee_currency TEXT",
        "market_fee_params_hash TEXT",
        "tx_hash TEXT",
    ],
    "owner_inventory_events": [
        "owner_account TEXT NOT NULL",
        "source_row_id TEXT NOT NULL",
        "source_row_hash TEXT NOT NULL",
        "event_ts_ms INTEGER",
        "condition_id TEXT NOT NULL",
        "asset_id TEXT NOT NULL",
        "outcome TEXT",
        "delta_size REAL",
        "position_size REAL",
        "avg_price REAL",
        "source_kind TEXT NOT NULL",
        "reconcile_snapshot_hash TEXT",
    ],
    "owner_redeem_settlement_events": [
        "owner_account TEXT NOT NULL",
        "source_row_id TEXT NOT NULL",
        "source_row_hash TEXT NOT NULL",
        "event_ts_ms INTEGER",
        "condition_id TEXT NOT NULL",
        "asset_id TEXT",
        "event_kind TEXT NOT NULL",
        "size REAL",
        "amount_usdc REAL",
        "tx_hash TEXT",
    ],
    "market_fee_params": [
        "condition_id TEXT NOT NULL",
        "market_slug TEXT",
        "source_row_id TEXT NOT NULL",
        "source_row_hash TEXT NOT NULL",
        "maker_fee_bps REAL",
        "taker_fee_bps REAL",
        "fee_rate_bps REAL",
        "fee_formula_version TEXT",
        "raw_params_hash TEXT NOT NULL",
    ],
    "owner_pnl_reconciliation": [
        "owner_account TEXT NOT NULL",
        "run_id TEXT NOT NULL",
        "inventory_reconciled INTEGER NOT NULL",
        "pnl_reconciled INTEGER NOT NULL",
        "after_fee_pnl REAL",
        "residual_lot_count INTEGER",
        "manifest_hash TEXT NOT NULL",
    ],
}


@dataclasses.dataclass(frozen=True, slots=True)
class OwnerSourceRow:
    source_row_id: str
    source_kind: str
    source_channel: str
    capture_ts_ms: int
    owner_account: str
    source_hash: str
    source_hash_algo: str
    raw_payload_json: str

    def asdict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def utc_now_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def canonicalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return canonicalize(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, set):
        return [canonicalize(item) for item in sorted(value)]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _pick(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _upper(value: Any) -> str | None:
    text = _text(value)
    return text.upper() if text else None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    abs_n = abs(n)
    if abs_n >= 1_000_000_000_000_000_000:
        return int(n / 1_000_000)
    if abs_n >= 1_000_000_000_000_000:
        return int(n / 1_000)
    if abs_n >= 1_000_000_000_000:
        return int(n)
    if abs_n >= 1_000_000_000:
        return int(n * 1000)
    return int(n)


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value is not None}


def make_owner_source_row(
    *,
    raw_payload: Mapping[str, Any],
    source_kind: str,
    source_channel: str,
    owner_account: str,
    source_row_id: str | None = None,
    capture_ts_ms: int | None = None,
) -> OwnerSourceRow:
    """Build the immutable source provenance row for one authenticated owner source row."""

    if not owner_account:
        raise ValueError("owner_account is required for owner truth source rows")
    capture_ms = capture_ts_ms if capture_ts_ms is not None else utc_now_ms()
    source_id = source_row_id or str(_pick(raw_payload, "source_row_id", "id", "order_id", "trade_id", "tx_hash") or "")
    if not source_id:
        source_id = canonical_hash({"source_kind": source_kind, "payload": raw_payload})[:24]
    hash_payload = {
        "schema_version": SCHEMA_VERSION,
        "source_kind": source_kind,
        "source_channel": source_channel,
        "owner_account": owner_account,
        "source_row_id": source_id,
        "raw_payload": raw_payload,
    }
    return OwnerSourceRow(
        source_row_id=source_id,
        source_kind=source_kind,
        source_channel=source_channel,
        capture_ts_ms=capture_ms,
        owner_account=owner_account,
        source_hash=canonical_hash(hash_payload),
        source_hash_algo=SOURCE_HASH_ALGO,
        raw_payload_json=canonical_json(raw_payload),
    )


def normalize_owner_order_event(raw: Mapping[str, Any], source: OwnerSourceRow) -> dict[str, Any]:
    size = _float(_pick(raw, "size", "original_size", "originalSize", "quantity"))
    filled = _float(_pick(raw, "filled_size", "filledSize", "size_matched", "sizeMatched", "matched_amount"))
    remaining = _float(_pick(raw, "remaining_size", "remainingSize", "remaining"))
    if remaining is None and size is not None and filled is not None:
        remaining = max(0.0, size - filled)
    lifecycle = _upper(_pick(raw, "lifecycle_state", "event_type", "type", "status")) or "UNKNOWN"
    return _compact(
        {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "owner_order_events",
            "owner_account": source.owner_account,
            "source_row_id": source.source_row_id,
            "source_row_hash": source.source_hash,
            "event_ts_ms": _int_ms(_pick(raw, "event_ts_ms", "timestamp", "created_at", "createdAt", "updated_at")),
            "condition_id": _text(_pick(raw, "condition_id", "conditionId", "market")),
            "market_slug": _text(_pick(raw, "market_slug", "marketSlug", "slug")),
            "asset_id": _text(_pick(raw, "asset_id", "assetId", "asset", "token_id")),
            "order_id": _text(_pick(raw, "order_id", "orderId", "id")),
            "client_order_id": _text(_pick(raw, "client_order_id", "clientOrderId")),
            "lifecycle_state": lifecycle,
            "market_side": _upper(_pick(raw, "market_side", "outcome", "side_label")),
            "direction": _upper(_pick(raw, "direction", "side", "order_side", "orderSide")),
            "price": _float(_pick(raw, "price")),
            "size": size,
            "filled_size": filled,
            "remaining_size": remaining,
            "maker_taker": _upper(_pick(raw, "maker_taker", "makerTaker", "liquidity", "trader_side")),
            "status": _text(_pick(raw, "status")),
            "tx_hash": _text(_pick(raw, "tx_hash", "txHash", "transactionHash")),
        }
    )


def normalize_market_fee_params(raw: Mapping[str, Any], source: OwnerSourceRow) -> dict[str, Any]:
    row = _compact(
        {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "market_fee_params",
            "condition_id": _text(_pick(raw, "condition_id", "conditionId", "market")),
            "market_slug": _text(_pick(raw, "market_slug", "marketSlug", "slug")),
            "source_row_id": source.source_row_id,
            "source_row_hash": source.source_hash,
            "maker_fee_bps": _float(_pick(raw, "maker_fee_bps", "makerFeeBps")),
            "taker_fee_bps": _float(_pick(raw, "taker_fee_bps", "takerFeeBps")),
            "fee_rate_bps": _float(_pick(raw, "fee_rate_bps", "feeRateBps", "fee")),
            "fee_formula_version": _text(_pick(raw, "fee_formula_version", "feeFormulaVersion", "version")),
            "raw_params_hash": canonical_hash(raw),
        }
    )
    if not row.get("condition_id"):
        raise ValueError("market fee params require condition_id")
    return row


def normalize_owner_fill_event(
    raw: Mapping[str, Any],
    source: OwnerSourceRow,
    *,
    fee_params_by_condition: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    condition_id = _text(_pick(raw, "condition_id", "conditionId", "market"))
    asset_id = _text(_pick(raw, "asset_id", "assetId", "asset", "token_id"))
    price = _float(_pick(raw, "price"))
    size = _float(_pick(raw, "size", "amount", "matched_amount", "matchedAmount"))
    if not condition_id or not asset_id or price is None or size is None:
        raise ValueError("owner fill requires condition_id, asset_id, price, and size")
    fee_amount = _float(_pick(raw, "fee_amount", "feeAmount"))
    fee_rate_bps = _float(_pick(raw, "fee_rate_bps", "feeRateBps"))
    fee_params_hash = _text(_pick(raw, "market_fee_params_hash", "feeParamsHash"))
    if not fee_params_hash and fee_params_by_condition:
        fee_params = fee_params_by_condition.get(condition_id)
        if fee_params:
            fee_params_hash = _text(fee_params.get("raw_params_hash")) or canonical_hash(fee_params)
            if fee_rate_bps is None:
                maker_taker_hint = _upper(_pick(raw, "maker_taker", "makerTaker", "liquidity", "trader_side"))
                if maker_taker_hint == "MAKER":
                    fee_rate_bps = _float(fee_params.get("maker_fee_bps"))
                elif maker_taker_hint == "TAKER":
                    fee_rate_bps = _float(fee_params.get("taker_fee_bps"))
                fee_rate_bps = fee_rate_bps if fee_rate_bps is not None else _float(fee_params.get("fee_rate_bps"))
    return _compact(
        {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "owner_fill_events",
            "owner_account": source.owner_account,
            "source_row_id": source.source_row_id,
            "source_row_hash": source.source_hash,
            "event_ts_ms": _int_ms(_pick(raw, "event_ts_ms", "match_ts_ms", "matchtime", "timestamp", "time")),
            "condition_id": condition_id,
            "asset_id": asset_id,
            "order_id": _text(_pick(raw, "order_id", "orderId")),
            "fill_id": _text(_pick(raw, "fill_id", "fillId", "id")),
            "trade_id": _text(_pick(raw, "trade_id", "tradeId", "id")),
            "market_side": _upper(_pick(raw, "market_side", "outcome", "side_label")),
            "direction": _upper(_pick(raw, "direction", "side", "order_side", "orderSide")),
            "maker_taker": _upper(_pick(raw, "maker_taker", "makerTaker", "liquidity", "trader_side")) or "UNKNOWN",
            "price": price,
            "size": size,
            "notional": _float(_pick(raw, "notional")) or round(price * size, 12),
            "fee_amount": fee_amount,
            "fee_rate_bps": fee_rate_bps,
            "fee_currency": _text(_pick(raw, "fee_currency", "feeCurrency")) or ("USDC" if fee_amount is not None else None),
            "market_fee_params_hash": fee_params_hash,
            "tx_hash": _text(_pick(raw, "tx_hash", "txHash", "transactionHash")),
        }
    )


def normalize_owner_inventory_event(raw: Mapping[str, Any], source: OwnerSourceRow) -> dict[str, Any]:
    condition_id = _text(_pick(raw, "condition_id", "conditionId", "market"))
    asset_id = _text(_pick(raw, "asset_id", "assetId", "asset", "token_id"))
    if not condition_id or not asset_id:
        raise ValueError("owner inventory event requires condition_id and asset_id")
    return _compact(
        {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "owner_inventory_events",
            "owner_account": source.owner_account,
            "source_row_id": source.source_row_id,
            "source_row_hash": source.source_hash,
            "event_ts_ms": _int_ms(_pick(raw, "event_ts_ms", "timestamp", "time")),
            "condition_id": condition_id,
            "asset_id": asset_id,
            "outcome": _upper(_pick(raw, "outcome", "market_side", "side")),
            "delta_size": _float(_pick(raw, "delta_size", "deltaSize")),
            "position_size": _float(_pick(raw, "position_size", "positionSize", "size", "amount")),
            "avg_price": _float(_pick(raw, "avg_price", "avgPrice", "averagePrice")),
            "source_kind": _text(_pick(raw, "source_kind", "sourceKind")) or "unknown",
            "reconcile_snapshot_hash": _text(_pick(raw, "reconcile_snapshot_hash", "snapshotHash")),
        }
    )


def normalize_owner_redeem_settlement_event(raw: Mapping[str, Any], source: OwnerSourceRow) -> dict[str, Any]:
    condition_id = _text(_pick(raw, "condition_id", "conditionId", "market"))
    if not condition_id:
        raise ValueError("owner redeem/settlement event requires condition_id")
    event_kind = _upper(_pick(raw, "event_kind", "eventKind", "type")) or "UNKNOWN"
    return _compact(
        {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "owner_redeem_settlement_events",
            "owner_account": source.owner_account,
            "source_row_id": source.source_row_id,
            "source_row_hash": source.source_hash,
            "event_ts_ms": _int_ms(_pick(raw, "event_ts_ms", "timestamp", "time")),
            "condition_id": condition_id,
            "asset_id": _text(_pick(raw, "asset_id", "assetId", "asset", "token_id")),
            "event_kind": event_kind,
            "size": _float(_pick(raw, "size", "amount", "shares")),
            "amount_usdc": _float(_pick(raw, "amount_usdc", "amountUsdc", "proceeds")),
            "tx_hash": _text(_pick(raw, "tx_hash", "txHash", "transactionHash")),
        }
    )


def source_hashes_present(rows: Iterable[Mapping[str, Any]]) -> bool:
    return all(bool(row.get("source_row_id")) and bool(row.get("source_row_hash")) for row in rows)


def _fee_is_recomputable(fill: Mapping[str, Any], fee_params_by_condition: Mapping[str, Mapping[str, Any]]) -> bool:
    if fill.get("fee_amount") is not None:
        return True
    if fill.get("fee_rate_bps") is not None:
        return True
    if fill.get("market_fee_params_hash"):
        return True
    condition_id = str(fill.get("condition_id") or "")
    return bool(condition_id and fee_params_by_condition.get(condition_id))


def evaluate_owner_truth_readiness(
    *,
    order_events: list[Mapping[str, Any]],
    fill_events: list[Mapping[str, Any]],
    inventory_events: list[Mapping[str, Any]],
    redeem_settlement_events: list[Mapping[str, Any]] | None = None,
    market_fee_params: list[Mapping[str, Any]] | None = None,
    inventory_reconciled: bool = False,
    pnl_reconciled: bool = False,
) -> dict[str, Any]:
    """Return the private_truth_ready gate for future owner execution records."""

    redeem_settlement_events = redeem_settlement_events or []
    market_fee_params = market_fee_params or []
    fee_params_by_condition = {
        str(row.get("condition_id")): row for row in market_fee_params if row.get("condition_id")
    }
    checks = {
        "has_owner_orders": bool(order_events),
        "has_owner_fills": bool(fill_events),
        "has_owner_inventory": bool(inventory_events),
        "orders_source_hash_ok": source_hashes_present(order_events),
        "fills_source_hash_ok": source_hashes_present(fill_events),
        "inventory_source_hash_ok": source_hashes_present(inventory_events),
        "redeem_settlement_source_hash_ok": source_hashes_present(redeem_settlement_events),
        "fee_params_source_hash_ok": source_hashes_present(market_fee_params),
        "fills_have_fee_truth_or_recompute_fields": all(
            _fee_is_recomputable(fill, fee_params_by_condition) for fill in fill_events
        ),
        "inventory_reconciled": bool(inventory_reconciled),
        "pnl_reconciled": bool(pnl_reconciled),
    }
    required = [
        "has_owner_orders",
        "has_owner_fills",
        "has_owner_inventory",
        "orders_source_hash_ok",
        "fills_source_hash_ok",
        "inventory_source_hash_ok",
        "fills_have_fee_truth_or_recompute_fields",
        "inventory_reconciled",
        "pnl_reconciled",
    ]
    failed = [name for name in required if not checks[name]]
    private_truth_ready = not failed
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "layer": PRIVATE_TRUTH_LAYER,
        "private_truth_ready": private_truth_ready,
        "checks": checks,
        "failed_checks": failed,
        "record_counts": {
            "owner_order_events": len(order_events),
            "owner_fill_events": len(fill_events),
            "owner_inventory_events": len(inventory_events),
            "owner_redeem_settlement_events": len(redeem_settlement_events),
            "market_fee_params": len(market_fee_params),
        },
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    return manifest


def owner_truth_scoring_interface() -> dict[str, Any]:
    """Machine-readable contract exposed to validation/scoring code."""

    return {
        "schema_version": SCHEMA_VERSION,
        "layer": PRIVATE_TRUTH_LAYER,
        "required_record_kinds": sorted(REQUIRED_RECORD_KINDS),
        "optional_record_kinds": ["owner_redeem_settlement_events", "market_fee_params"],
        "table_schemas": OWNER_TRUTH_TABLE_SCHEMAS,
        "private_truth_ready_rule": {
            "orders": "non-empty and every row has source_row_id/source_row_hash",
            "fills": "non-empty, every row has source_row_id/source_row_hash, maker_taker, and actual fee or recomputable fee fields",
            "inventory": "non-empty, every row has source_row_id/source_row_hash, and ledger reconciles to position snapshot",
            "pnl": "after-fee PnL reconciliation must pass",
            "historical_shadow": "never upgrades to private_truth_ready=true without future authenticated owner execution rows",
        },
    }
