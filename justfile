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

# Remove generated scratch: *.wav under out/ + experiments scratch (pass --json to also remove out/*.json)
clean *FLAGS:
    #!/usr/bin/env bash
    find out -type f -name '*.wav' -delete 2>/dev/null || true
    # experiments scratch (gitignored generated files; leaves committed *.py prototypes)
    find experiments -type f -name '*.wav' -delete 2>/dev/null || true
    find experiments -type f -name '_*.txt' -delete 2>/dev/null || true
    find experiments -type f -name 'CHECKPOINT_*' -delete 2>/dev/null || true
    if echo "{{FLAGS}}" | grep -q -- --json; then
        find out -type f -name '*.json' -delete 2>/dev/null || true
    fi
