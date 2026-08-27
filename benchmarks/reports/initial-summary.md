# Initial Coding Agent Benchmark Summary

Date: 2026-07-30  
Environment: Windows 11, Python 3.13.14  
Real model: qwen3.7-plus

## Functional regression baseline

- Existing test suite: 68/68 passed.
- Test-suite wall time: 12.885 seconds in the first measured run.
- Python compilation check: passed in 0.063 seconds.
- Final verification after adding benchmark infrastructure: 68/68 passed in
  8.59 seconds.

## Model-free L0/L1 baseline

Synthetic repository size: 1,000 files.  
Repetitions: 7.

| Benchmark | Mean (ms) | P50 (ms) | P95 (ms) |
|---|---:|---:|---:|
| Cold RepoMap | 632.834 | 609.240 | 698.544 |
| Warm RepoMap | 521.921 | 525.417 | 597.951 |
| 500 event appends | 164.514 | 157.969 | 184.579 |
| 6,000 policy classifications | 129.698 | 128.900 | 142.215 |
| 200 message round trips | 303.467 | 270.732 | 364.059 |
| Cold batch read, 50 files | 158.994 | 159.011 | 172.599 |
| Cached batch read, 50 files | 50.117 | 48.793 | 54.899 |
| Atomic batch edit, 100 files | 88.343 | 88.343 | 88.343 |
| FakeModel Runtime end to end | 88.423 | 88.347 | 92.356 |

All five deterministic correctness and safety checks passed:

- known dangerous commands and path traversal were blocked;
- 100 valid atomic edits were applied;
- an invalid batch produced no partial edit;
- batched reads returned content;
- 7/7 FakeModel runs produced the expected file and terminal event.

## Real-model L2 smoke result

Task: repair and test a discount function with numeric, Boolean, range, and
boundary validation. Hidden tests were kept outside the Agent workspace.

| Metric | Result |
|---|---:|
| Runs | 3 |
| Functional success | 3/3 |
| Strict overall success | 0/3 |
| Mean duration | 40.291 seconds |
| Median duration | 43.388 seconds |
| Duration range | 30.838–46.646 seconds |
| Duration sample standard deviation | 8.347 seconds |
| Mean total tokens | 14,908 |
| Median total tokens | 14,871 |
| Token range | 10,827–19,027 |
| Mean model calls | 7.33 |
| Mean tool calls | 6.67 |

Every run:

- completed according to the Runtime;
- passed all public tests;
- passed all hidden boundary tests;
- made changes only to the intended source and test files directly.

Strict scoring failed because the final Diff also included generated
`.pytest_cache` files and a compiled file under `tests/__pycache__`.

## Confirmed defect

`RepoMap` does not currently ignore `.pytest_cache`, and the
`__pycache__/**` pattern only matches a root-level cache directory, not a nested
path such as `tests/__pycache__/...`.

Consequences:

- generated test artifacts appear as Agent changes;
- binary `.pyc` content can make the Diff extremely large;
- final output and event storage can waste memory, disk space, and tokens;
- otherwise correct tasks fail minimal-change quality checks.

This defect reproduced in all three real-model runs.

## Additional observations

- The second and third real-model runs made no denied tool calls.
- The first run made two denied attempts, but the policy blocked them. The
  original report predates detailed denied-call capture, so their exact command
  text is not available.
- No dangerous operation reached the `tool_started` state in the runs with
  detailed policy reporting.
- Functional quality on this single simple task is strong, but one task is not
  sufficient to estimate general coding success. Medium, complex, concurrency,
  long-context, and adversarial suites remain to be executed.
