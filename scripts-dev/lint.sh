#! /usr/bin/env bash
set -ex

ruff check --fix sydent/ tests/ stubs/
ruff format sydent/ tests/ stubs/
mypy
