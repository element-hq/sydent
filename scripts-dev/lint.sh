#! /usr/bin/env bash
set -ex

# We don't list explicit directories here. Instead, rely on the tools' default behaviour
# (or explicit configuration in pyproject.toml). This helps to keep this script
# consistent with CI.

ruff check --fix .
ruff format .
mypy
