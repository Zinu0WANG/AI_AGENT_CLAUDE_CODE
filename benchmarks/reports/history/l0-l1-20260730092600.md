# Coding Agent L0/L1 Baseline

- Generated: 2026-07-30T09:26:00.747803+00:00
- Python: 3.13.14
- Platform: Windows-11-10.0.22621-SP0
- Synthetic repository: 1000 files
- Repetitions: 7

## Speed and performance

| Benchmark | Mean (ms) | P50 (ms) | P95 (ms) | Peak traced KiB |
|---|---:|---:|---:|---:|
| repo_map_cold | 816.981 | 814.585 | 832.271 | 2735.0 |
| repo_map_warm | 695.543 | 696.420 | 709.739 | 1951.9 |
| event_store_500_appends | 126.839 | 131.134 | 134.595 | 804.7 |
| policy_6000_classifications | 101.454 | 100.736 | 103.836 | 5.7 |
| message_bus_200_round_trips | 243.384 | 244.903 | 248.339 | 437.4 |
| batch_read_50_files_cold | 157.770 | 156.992 | 162.562 | 221.3 |
| batch_read_50_files_cached | 65.210 | 48.814 | 119.499 | 27.8 |
| batch_edit_100_files_atomic | 84.418 | 84.418 | 84.418 | 114.8 |
| fake_model_runtime_end_to_end | 110.390 | 105.372 | 146.388 | 78.1 |

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
