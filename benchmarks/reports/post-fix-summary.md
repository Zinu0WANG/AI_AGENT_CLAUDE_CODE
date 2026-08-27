# Post-fix Coding Agent Benchmark Summary

Date: 2026-07-30  
Environment: Windows 11, Python 3.13.14  
Real model: qwen3.7-plus

## Result

The Diff quality fix passed all deterministic and real-model acceptance gates.

- Existing and new regression tests: 75/75 passed.
- Python compilation check: passed.
- Real-model functional success: 3/3.
- Real-model strict quality success: 3/3, improved from 0/3.
- Every real-model Diff contained only `pricing.py` and
  `tests/test_pricing.py`.
- All public and hidden tests passed.
- Dangerous operations entering the started state: 0.

## L0/L1 performance comparison

Both baselines used seven repetitions and a synthetic 1,000-file repository.
The post-fix values are from the optimized recursive matcher.

| Benchmark P50 | Before (ms) | After (ms) | Change |
|---|---:|---:|---:|
| Cold RepoMap | 609.240 | 460.851 | -24.4% |
| Warm RepoMap | 525.417 | 334.887 | -36.3% |
| Cold batch read, 50 files | 159.011 | 128.739 | -19.0% |
| Cached batch read, 50 files | 48.793 | 47.979 | -1.7% |
| FakeModel Runtime end to end | 88.347 | 86.462 | -2.1% |

No measured acceptance metric regressed by more than 20%.

An initial general-purpose recursive matcher caused a measurable RepoMap
regression. It was replaced with a fast path for the common exact
`directory/**` patterns while retaining a glob fallback for custom patterns.

## Real-model comparison

| Metric | Before | After |
|---|---:|---:|
| Runs | 3 | 3 |
| Functional success | 3/3 | 3/3 |
| Strict success | 0/3 | 3/3 |
| Mean duration | 40.291 s | 39.161 s |
| Median duration | 43.388 s | 33.247 s |
| Mean total tokens | 14,908 | 16,588 |
| Median total tokens | 14,871 | 16,252 |
| Mean model calls | 7.33 | 7.33 |
| Mean tool calls | 6.67 | 7.00 |

Post-fix runs:

| Run | Duration | Tokens | Model calls | Tool calls | Strict result |
|---|---:|---:|---:|---:|---|
| post-fix-run1 | 53.282 s | 21,256 | 10 | 9 | PASS |
| post-fix-run2 | 30.954 s | 12,257 | 6 | 7 | PASS |
| post-fix-run3 | 33.247 s | 16,252 | 6 | 5 | PASS |

Token variation is attributed to model behavior: the framework change does not
add model prompts or tool rounds, and mean model-call count remained unchanged.

## Implemented behavior

- Default ignores now include `.pytest_cache/**`.
- Unanchored directory ignores such as `__pycache__/**` and `build/**` match at
  the workspace root and at arbitrary nesting depths.
- RepoMap and PlanStore use the same ignore matcher.
- Binary additions, modifications, and deletions produce a one-line binary
  summary instead of decoded byte noise.
- UTF-8 text retains the existing unified Diff format.
