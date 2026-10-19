Three things to know before running:

- **Expected result at commit 2:** everything green except the two `toolex (unit)` tests, which report `XFAIL` (or `SKIPPED` if `toolex`/`requests` can't be imported). That is the passing state for this commit.
- **Run it from the repo root** (`pytest test_minishell.py -q`) so `minishell_tools`, `tooling`, and `toolex` resolve on `sys.path`.
- **The xfail markers are load-bearing:** they encode the commit-3 contract I described last turn (bare grant → all declared spec caps; unknown caps dropped; `_serialize_result` seam). When you wire toolex, the strict xfails will XPASS and fail the suite — remove the markers at that point, deliberately.
