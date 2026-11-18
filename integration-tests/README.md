matrix-is-tester
================

Black-box integration tests for Sydent, run against a real Sydent process.
They exercise the Matrix Identity Service API from the outside, so
Sydent-specific behaviour belongs in the unit tests under `tests/` instead.

`matrix_is_tester.launcher.MatrixIsTestLauncher` starts Sydent with the Python
interpreter that runs the tests and a generated config. A fake homeserver and
an SMTP sink run in child processes. The suite uses fixed local ports (8098,
8099, 4490 and 9925), so only one run can happen at a time on a machine.

To run the suite from the repository root:

```sh
uv run trial matrix_is_tester
```

Each Sydent's stderr is written to `matrix_is_tester/sydent-<port>.stderr`.

The code was copied from
[matrix-org/matrix-is-tester](https://github.com/matrix-org/matrix-is-tester).
Those files remain under the Apache License 2.0 in `LICENSE`.
