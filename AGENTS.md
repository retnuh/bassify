# Agent notes

## Formatting Python

Never hand-format Python code or manually chase line-length/style diffs.
Run the linter in fix mode instead:

```
uv run ruff format .
uv run ruff check --fix .
```

Only fix something by hand when ruff genuinely can't auto-fix it (e.g. an
ambiguous-name rename like E741, which needs a human/agent decision on the
new name) — that should be rare. Confirm clean with:

```
uv run ruff check .
uv run ruff format --check .
```
