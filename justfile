# List recipes
default:
    @just --list

install:
    uv sync

lint:
    uv run ruff check .

fmt:
    uv run ruff format .

test:
    uv run pytest -q

check: lint test

# Stage passthroughs (args forwarded), e.g. `just run tracks/BluesBass/01_*.mp3`
extract *ARGS:
    uv run bassify extract {{ARGS}}

detect *ARGS:
    uv run bassify detect {{ARGS}}

combine *ARGS:
    uv run bassify combine {{ARGS}}

remix *ARGS:
    uv run bassify remix {{ARGS}}

encode *ARGS:
    uv run bassify encode {{ARGS}}

run *ARGS:
    uv run bassify run {{ARGS}}

# Remove intermediate *.wav under out/ (pass --json to also remove *.json)
clean-intermediates *FLAGS:
    #!/usr/bin/env bash
    find out -type f -name '*.wav' -delete 2>/dev/null || true
    if echo "{{FLAGS}}" | grep -q -- --json; then
        find out -type f -name '*.json' -delete 2>/dev/null || true
    fi
