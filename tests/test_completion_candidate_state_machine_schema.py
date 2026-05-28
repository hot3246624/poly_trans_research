import importlib.util
import sys
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


if __name__ == "__main__":
    unittest.main()
