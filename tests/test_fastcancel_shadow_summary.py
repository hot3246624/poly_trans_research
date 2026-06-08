import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "summarize_fastcancel_shadow_events.py"
SPEC = importlib.util.spec_from_file_location("fastcancel_shadow_summary", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
fastcancel_shadow_summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fastcancel_shadow_summary)


def _config() -> dict:
    return {
        "shadow_pass_fail": {
            "minimum_observation_window": "3 full BTC trading days; prefer 5 full days",
            "candidate_count_min_per_day": 20,
        },
        "required_shadow_events": ["fastcancel_episode_summary"],
        "required_shadow_fields": ["day", "path", "effective_clip"],
    }


def _combo_summary() -> dict:
    return {
        "l2_completion_reprice": {
            "base": {"pnl": 120.0, "positive_days": "3/3"},
            "slippage": {"0.02": {"pnl": 90.0, "positive_days": "3/3"}},
        },
        "robustness": {"l2_slippage": {"max_tested_slippage_all_days_positive": 0.02}},
    }


def _episode(day: str, idx: int, **overrides: object) -> dict:
    event = {
        "event_type": "fastcancel_episode_summary",
        "day": day,
        "path": "completion",
        "window_name": "late",
        "proxy_first_fill": True,
        "raw_replay_pnl": 1.0,
        "effective_clip": 60,
        "completion_vwap_drift": None,
        "shadow_pnl_l2": None,
        "shadow_pnl_with_2c_friction": None,
        "actual_first_fill_ts_ms": None,
        "actual_first_fill_qty": None,
        "event_seq": idx,
    }
    event.update(overrides)
    return event


class FastcancelShadowSummaryTests(unittest.TestCase):
    def test_replay_ready_does_not_promote_without_execution_truth(self) -> None:
        events = []
        idx = 1
        for day in ["2026-04-27", "2026-04-28", "2026-04-29"]:
            for _ in range(20):
                events.append(_episode(day, idx))
                idx += 1

        summary = fastcancel_shadow_summary.summarize(events, _config(), _combo_summary())

        self.assertTrue(summary["verdict"]["replay_ready_for_live_shadow"])
        self.assertFalse(summary["verdict"]["own_execution_truth_ready"])
        self.assertFalse(summary["verdict"]["promote_to_enforce_discussion"])
        self.assertEqual(summary["gate_results"]["completion_vwap_drift_pass"], "unknown")
        self.assertTrue(summary["gate_results"]["event_schema_pass"])

    def test_promotes_only_when_truth_and_drift_and_2c_pnl_exist(self) -> None:
        events = []
        idx = 1
        for day in ["2026-04-27", "2026-04-28", "2026-04-29"]:
            for _ in range(20):
                events.append(
                    _episode(
                        day,
                        idx,
                        actual_first_fill_ts_ms=1777275000000 + idx,
                        actual_first_fill_qty=60,
                        completion_vwap_drift=0.005,
                        shadow_pnl_l2=1.0,
                        shadow_pnl_with_2c_friction=1.0,
                    )
                )
                idx += 1

        summary = fastcancel_shadow_summary.summarize(events, _config(), _combo_summary())

        self.assertTrue(summary["verdict"]["replay_ready_for_live_shadow"])
        self.assertTrue(summary["verdict"]["own_execution_truth_ready"])
        self.assertTrue(summary["gate_results"]["completion_vwap_drift_pass"])
        self.assertEqual(summary["shadow_pnl_l2"], 60.0)
        self.assertEqual(summary["shadow_pnl_with_2c_friction"], 60.0)
        self.assertTrue(summary["verdict"]["promote_to_enforce_discussion"])

    def test_missing_required_episode_field_blocks_replay_ready(self) -> None:
        config = _config()
        config["required_shadow_fields"] = ["missing_field"]
        events = []
        idx = 1
        for day in ["2026-04-27", "2026-04-28", "2026-04-29"]:
            for _ in range(20):
                events.append(_episode(day, idx))
                idx += 1

        summary = fastcancel_shadow_summary.summarize(events, config, _combo_summary())

        self.assertFalse(summary["gate_results"]["event_schema_pass"])
        self.assertFalse(summary["verdict"]["replay_ready_for_live_shadow"])
        self.assertEqual(summary["event_contract"]["missing_required_episode_fields"], ["missing_field"])


if __name__ == "__main__":
    unittest.main()
