# Coding Agent L0/L1 Baseline

- Generated: 2026-07-30T09:26:46.836408+00:00
- Python: 3.13.14
- Platform: Windows-11-10.0.22621-SP0
- Synthetic repository: 1000 files
- Repetitions: 7

## Speed and performance

| Benchmark | Mean (ms) | P50 (ms) | P95 (ms) | Peak traced KiB |
|---|---:|---:|---:|---:|
| repo_map_cold | 461.965 | 460.851 | 487.231 | 2734.9 |
| repo_map_warm | 341.450 | 334.887 | 369.235 | 1951.3 |
| event_store_500_appends | 130.067 | 126.067 | 146.665 | 804.4 |
| policy_6000_classifications | 120.033 | 120.001 | 137.985 | 5.7 |
| message_bus_200_round_trips | 258.128 | 257.504 | 275.629 | 437.4 |
| batch_read_50_files_cold | 135.668 | 128.739 | 151.206 | 220.7 |
| batch_read_50_files_cached | 47.645 | 47.979 | 54.547 | 27.4 |
| batch_edit_100_files_atomic | 93.253 | 93.253 | 93.253 | 114.9 |
| fake_model_runtime_end_to_end | 93.286 | 86.462 | 115.296 | 78.3 |

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
