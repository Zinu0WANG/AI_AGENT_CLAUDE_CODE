# Coding Agent L0/L1 Baseline

- Generated: 2026-07-30T09:25:33.086943+00:00
- Python: 3.13.14
- Platform: Windows-11-10.0.22621-SP0
- Synthetic repository: 1000 files
- Repetitions: 7

## Speed and performance

| Benchmark | Mean (ms) | P50 (ms) | P95 (ms) | Peak traced KiB |
|---|---:|---:|---:|---:|
| repo_map_cold | 990.682 | 1004.423 | 1035.551 | 2737.0 |
| repo_map_warm | 1330.582 | 1359.133 | 1432.597 | 1951.9 |
| event_store_500_appends | 232.285 | 232.009 | 235.898 | 806.7 |
| policy_6000_classifications | 219.589 | 217.533 | 236.253 | 5.7 |
| message_bus_200_round_trips | 336.632 | 340.883 | 395.680 | 437.4 |
| batch_read_50_files_cold | 301.520 | 314.634 | 335.967 | 221.3 |
| batch_read_50_files_cached | 83.841 | 81.072 | 90.538 | 27.5 |
| batch_edit_100_files_atomic | 156.719 | 156.719 | 156.719 | 114.8 |
| fake_model_runtime_end_to_end | 120.546 | 121.198 | 127.546 | 78.5 |

## Deterministic quality and safety checks

| Check | Result | Details |
|---|---|---|
| policy_blocks_known_dangerous_inputs | PASS | 3 dangerous commands and 1 path traversal checked |
| batch_edit_applies_all_valid_edits | PASS | 100 files checked |
| batch_edit_rolls_back_invalid_batch | PASS | earlier valid edit remained unapplied after a later invalid edit |
| batch_read_returned_content | PASS | output_length=6023 |
| fake_runtime_completion_quality | PASS | 7/7 runs completed with the expected file and terminal event |

Overall: **PASS**

> This report excludes real model latency, token cost, and semantic task quality.
