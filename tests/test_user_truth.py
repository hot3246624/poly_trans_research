import unittest
from unittest.mock import patch

from completion_first_data.capture.envelope import RawEnvelope
from completion_first_data.replay.normalize import normalize_fill_events
from completion_first_data.user_truth import (
    apply_fill_rows_to_inventory,
    build_user_subscribe_message,
    normalize_inventory_snapshot,
    resolve_user_auth_config,
)


class UserTruthHelpersTests(unittest.TestCase):
    def test_resolve_user_auth_accepts_legacy_aliases(self) -> None:
        fake_key = "0x" + ("11" * 32)
        env = {
            "POLYMARKET_BUILDER_API_KEY": "api-key",
            "POLYMARKET_BUILDER_SECRET": "api-secret",
            "POLYMARKET_BUILDER_PASSPHRASE": "api-pass",
            "POLYMARKET_FUNDER_ADDRESS": "0xabc",
            "POLYMARKET_PRIVATE_KEY": fake_key,
        }
        with patch("completion_first_data.user_truth._validate_api_creds", return_value=True):
            cfg = resolve_user_auth_config(env)
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg.auth_source, "api_creds")
        self.assertEqual(cfg.api_key, "api-key")
        self.assertEqual(cfg.funder_address, "0xabc")
        self.assertEqual(cfg.l1_private_key, fake_key)

    def test_build_user_subscribe_message_dedups_markets(self) -> None:
        msg = build_user_subscribe_message(["0x1", "0x1", "0x2", "", "0x2"])
        self.assertEqual(msg["operation"], "subscribe")
        self.assertEqual(msg["markets"], ["0x1", "0x2"])

    def test_normalize_fill_events_handles_maker_orders(self) -> None:
        env = RawEnvelope(
            recv_unix_ms=1777174100000,
            recv_monotonic_ns=1,
            capture_seq=1,
            source="user_ws",
            channel="user_trade",
            condition_id="0xcond",
            payload_json={
                "event_type": "trade",
                "type": "TRADE",
                "id": "trade-1",
                "taker_order_id": "taker-1",
                "market": "0xcond",
                "asset_id": "asset-yes",
                "side": "BUY",
                "size": "10",
                "price": "0.57",
                "status": "MATCHED",
                "matchtime": "1672290701",
                "outcome": "YES",
                "maker_orders": [
                    {
                        "order_id": "maker-order-1",
                        "owner": "0xfunder",
                        "maker_address": "0xmaker",
                        "matched_amount": "4",
                        "price": "0.57",
                        "fee_rate_bps": "0",
                        "asset_id": "asset-yes",
                        "outcome": "YES",
                        "side": "SELL",
                    }
                ],
                "trader_side": "MAKER",
                "capture_funder_address": "0xfunder",
            },
        )
        rows = normalize_fill_events(env)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_id"], "trade-1")
        self.assertEqual(rows[0]["order_id"], "maker-order-1")
        self.assertEqual(rows[0]["direction"], "SELL")
        self.assertEqual(rows[0]["market_side"], "YES")
        self.assertEqual(rows[0]["size"], 4.0)

    def test_inventory_snapshot_and_fill_apply(self) -> None:
        snap = normalize_inventory_snapshot(
            {
                "conditionId": "0xcond",
                "asset": "asset-no",
                "outcome": "NO",
                "size": "5",
                "avgPrice": "0.41",
                "redeemable": False,
                "mergeable": True,
            },
            source_kind="bootstrap",
        )
        self.assertIsNotNone(snap)
        assert snap is not None

        state = {snap.asset_id: snap}
        rows = [
            {
                "condition_id": "0xcond",
                "asset_id": "asset-no",
                "market_side": "NO",
                "direction": "BUY",
                "size": 2.0,
                "price": 0.5,
            }
        ]
        derived = apply_fill_rows_to_inventory(state, rows)
        self.assertEqual(len(derived), 1)
        self.assertAlmostEqual(derived[0].size, 7.0)
        self.assertEqual(derived[0].source_kind, "derived_fill")

    def test_resolve_user_auth_falls_back_to_l1_derive_when_api_creds_invalid(self) -> None:
        fake_key = "0x" + ("22" * 32)
        env = {
            "POLYMARKET_BUILDER_API_KEY": "stale-key",
            "POLYMARKET_BUILDER_SECRET": "stale-secret",
            "POLYMARKET_BUILDER_PASSPHRASE": "stale-pass",
            "POLYMARKET_FUNDER_ADDRESS": "“0xabc\"",
            "POLYMARKET_PRIVATE_KEY": fake_key,
        }

        class _FakeCreds:
            api_key = "fresh-key"
            api_secret = "fresh-secret"
            api_passphrase = "fresh-pass"

        with patch("completion_first_data.user_truth._validate_api_creds", return_value=False), patch(
            "completion_first_data.user_truth._create_or_derive_api_creds",
            return_value=_FakeCreds(),
        ):
            cfg = resolve_user_auth_config(env)

        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg.auth_source, "derived_api_creds")
        self.assertEqual(cfg.api_key, "fresh-key")
        self.assertEqual(cfg.funder_address, "0xabc")


if __name__ == "__main__":
    unittest.main()
