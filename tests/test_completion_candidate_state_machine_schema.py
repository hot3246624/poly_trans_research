import importlib.util
import sys
import tempfile
import unittest
from collections import defaultdict
from types import SimpleNamespace
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_completion_candidate_state_machine.py"
SPEC = importlib.util.spec_from_file_location("completion_candidate_state_machine", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
state_machine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = state_machine
SPEC.loader.exec_module(state_machine)


class CompletionCandidateStateMachineSchemaTests(unittest.TestCase):
    def test_yes_no_side_fields_stay_varchar(self) -> None:
        rows = [
            {
                "candidate_id": "abc",
                "action_id": 1,
                "day": "2026-05-16",
                "condition_id": "0x1",
                "side": "YES",
                "opposite_side": "NO",
                "winner_side": "YES",
                "seed_qty": 1.25,
                "deployable": False,
            }
        ]
        fieldnames = [
            "candidate_id",
            "action_id",
            "day",
            "condition_id",
            "side",
            "opposite_side",
            "winner_side",
            "seed_qty",
            "deployable",
        ]
        con = duckdb.connect(":memory:")
        state_machine.create_table_from_rows(con, "candidate_registry", rows, fieldnames)
        schema = {row[0]: row[1] for row in con.execute("DESCRIBE candidate_registry").fetchall()}
        values = con.execute(
            "SELECT side, opposite_side, winner_side, seed_qty, deployable FROM candidate_registry"
        ).fetchone()

        self.assertEqual(schema["side"], "VARCHAR")
        self.assertEqual(schema["opposite_side"], "VARCHAR")
        self.assertEqual(schema["winner_side"], "VARCHAR")
        self.assertEqual(schema["seed_qty"], "DOUBLE")
        self.assertEqual(schema["deployable"], "BOOLEAN")
        self.assertEqual(values, ("YES", "NO", "YES", 1.25, False))

    def test_official_taker_fee_formula_is_applied(self) -> None:
        fee = state_machine.official_clob_taker_fee(10.0, 0.40, 0.07)
        self.assertAlmostEqual(fee, 0.168)

        metrics = defaultdict(float)
        metrics["active_markets"] = 1
        metrics["candidate_count"] = 1
        metrics["seed_actions"] = 1
        metrics["gross_buy_qty"] = 10.0
        metrics["gross_buy_cost"] = 4.0
        metrics["pair_qty"] = 5.0
        metrics["pair_actions"] = 1
        metrics["pair_cost_sum"] = 4.0
        metrics["net_pair_cost_sum"] = 4.0
        metrics["pair_pnl"] = 20.0
        metrics["total_fee"] = fee
        args = SimpleNamespace(
            fee_model="official_taker",
            official_fee_rate=0.07,
            flat_notional_fee_rate=0.0,
        )

        out = state_machine.finish_metrics(metrics, [], args)

        self.assertEqual(out["gross_pnl"], 20.0)
        self.assertEqual(out["fee283"], None)
        self.assertEqual(out["official_taker_fee"], 0.168)
        self.assertEqual(out["fee_after_pnl"], 19.832)
        self.assertEqual(out["net_pnl"], 19.832)

    def test_sizing_override_fields_keep_stable_types_with_zero_rows(self) -> None:
        fieldnames = [
            "candidate_id",
            "target_qty_effective",
            "max_open_cost_effective",
            "sizing_override_id",
            "sizing_override_key_type",
            "sizing_override_key",
        ]
        con = duckdb.connect(":memory:")
        state_machine.create_table_from_rows(con, "candidate_registry", [], fieldnames)
        schema = {row[0]: row[1] for row in con.execute("DESCRIBE candidate_registry").fetchall()}

        self.assertEqual(schema["target_qty_effective"], "DOUBLE")
        self.assertEqual(schema["max_open_cost_effective"], "DOUBLE")
        self.assertEqual(schema["sizing_override_id"], "VARCHAR")
        self.assertEqual(schema["sizing_override_key_type"], "VARCHAR")
        self.assertEqual(schema["sizing_override_key"], "VARCHAR")

    def test_sizing_overrides_resolve_by_priority_and_inherit_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sizing.csv"
            path.write_text(
                "sizing_override_id,candidate_row_id,condition_id,slug,target_qty,max_open_cost,enabled\n"
                "by_condition,,0xabc,,7,,true\n"
                "by_candidate,42,,,3,12,true\n"
                "disabled,,0xdisabled,,,,false\n",
                encoding="utf-8",
            )
            overrides = state_machine.load_sizing_overrides_csv(path)

        args = SimpleNamespace(target_qty=5.0, max_open_cost=50.0, sizing_overrides=overrides)
        by_candidate = state_machine.effective_sizing_for_row(
            args,
            {"candidate_row_id": 42, "condition_id": "0xabc", "slug": "slug-a"},
        )
        by_condition = state_machine.effective_sizing_for_row(
            args,
            {"candidate_row_id": 43, "condition_id": "0xabc", "slug": "slug-a"},
        )
        disabled = state_machine.effective_sizing_for_row(
            args,
            {"candidate_row_id": 44, "condition_id": "0xdisabled", "slug": "slug-b"},
        )
        defaulted = state_machine.effective_sizing_for_row(
            args,
            {"candidate_row_id": 45, "condition_id": "0xmissing", "slug": "slug-c"},
        )

        self.assertEqual(by_candidate.target_qty, 3.0)
        self.assertEqual(by_candidate.max_open_cost, 12.0)
        self.assertEqual(by_candidate.override_id, "by_candidate")
        self.assertEqual(by_candidate.override_key_type, "candidate_row_id")
        self.assertEqual(by_condition.target_qty, 7.0)
        self.assertEqual(by_condition.max_open_cost, 50.0)
        self.assertEqual(by_condition.override_id, "by_condition")
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.target_qty, 5.0)
        self.assertEqual(disabled.max_open_cost, 50.0)
        self.assertIsNone(defaulted.override_id)
        self.assertEqual(defaulted.target_qty, 5.0)
        self.assertEqual(defaulted.max_open_cost, 50.0)

    def test_sizing_overrides_reject_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_sizing.csv"
            path.write_text(
                "condition_id,target_qty,max_open_cost,enabled\n"
                "0xabc,-1,10,true\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "target_qty must be positive"):
                state_machine.load_sizing_overrides_csv(path)

            path.write_text(
                "condition_id,target_qty,max_open_cost,enabled\n"
                "0xabc,,10,maybe\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "enabled must be boolean"):
                state_machine.load_sizing_overrides_csv(path)


if __name__ == "__main__":
    unittest.main()
