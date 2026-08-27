# Coding Agent Benchmarks

This directory contains repeatable, model-free L0/L1 benchmarks for the coding
agent runtime. The benchmark uses synthetic repositories in temporary
directories and does not modify the repository under test.

Run the default baseline:

```powershell
python benchmarks/run_baseline.py
```

Use more repetitions or a larger synthetic repository:

```powershell
python benchmarks/run_baseline.py --repeats 10 --files 5000
```

Results are written to:

- `benchmarks/reports/latest.json`
- `benchmarks/reports/latest.md`

The benchmark covers:

- cold and warm repository-map construction;
- event-store append throughput;
- command-policy classification;
- SQLite teammate-message round trips;
- batched file reads and atomic batched edits;
- complete `AgentRuntime` runs driven by `FakeModel`;
- deterministic correctness and safety checks.

These results measure the harness itself. Real-provider latency, token cost, and
task success rate belong to the L2 benchmark and must be reported separately.

Run the real-provider L2 smoke task:

```powershell
python benchmarks/run_l2_smoke.py
```

This uses the provider configured in the repository `.env`, operates only on a
temporary task repository, runs public tests, and then evaluates hidden boundary
tests that are never placed in the Agent workspace.
