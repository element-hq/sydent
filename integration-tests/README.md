matrix-is-tester
================

matrix-is-tester is an integration testing system for Matrix Identity servers, similar
to sytest (although using python rather than perl).

To launch the IS server, it attempts to import
`matrix_is_test.launcher.MatrixIsTestLauncher`. This must be provided separately
for the specific ID server implementation to be tested. See sydent's implementation
for an example of how this works.

Sydent supplies its matrix-is-tester launcher in the project directory, so to launch
matrix_is_tester on sydent you would run:

```
PYTHONPATH="/path/to/sydent" trial matrix_is_tester
```

...which puts the launcher on the PYTHONPATH and invokes trial on matrix_is_tester (which
is assumed to already be on sys.path).
