# Coding Agent L0/L1 Baseline

- Generated: 2026-07-30T09:26:00.711101+00:00
- Python: 3.13.14
- Platform: Windows-11-10.0.22621-SP0
- Synthetic repository: 1000 files
- Repetitions: 7

## Speed and performance

| Benchmark | Mean (ms) | P50 (ms) | P95 (ms) | Peak traced KiB |
|---|---:|---:|---:|---:|
| repo_map_cold | 808.529 | 802.599 | 835.454 | 2736.6 |
| repo_map_warm | 699.928 | 694.150 | 710.713 | 1951.9 |
| event_store_500_appends | 128.370 | 129.227 | 137.910 | 817.7 |
| policy_6000_classifications | 101.881 | 101.921 | 104.216 | 5.7 |
| message_bus_200_round_trips | 242.390 | 244.377 | 248.623 | 437.4 |
| batch_read_50_files_cold | 160.215 | 158.022 | 168.166 | 221.3 |
| batch_read_50_files_cached | 61.569 | 47.988 | 86.916 | 27.4 |
| batch_edit_100_files_atomic | 83.936 | 83.936 | 83.936 | 116.1 |
| fake_model_runtime_end_to_end | 106.718 | 94.403 | 141.104 | 78.5 |

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
