set shell := ["bash", "-cu"]

# Start the game
run:
  uv run python -m tetris.main

# Build a distribution (wheel + sdist) into dist/
build:
  uv build

# Tag a release and build it (e.g. `just release 1.2.0` or `just release v1.2.0`)
# Creates an annotated git tag, builds dist/, and pushes the tag if an origin remote exists.
release version:
  #!/usr/bin/env bash
  set -euo pipefail
  ver="${version#v}"                 # strip an optional leading v
  tag="v${ver}"                      # hatch-vcs maps v1.2.0 -> 1.2.0

  if git rev-parse "refs/tags/${tag}" >/dev/null 2>&1; then
    echo "Tag ${tag} already exists — aborting." >&2
    exit 1
  fi
  if [ -n "$(git status --porcelain)" ]; then
    echo "Working tree is dirty — commit or stash before releasing." >&2
    exit 1
  fi

  git tag -a "${tag}" -m "Release ${tag}"
  uv build

  if git remote get-url origin >/dev/null 2>&1; then
    git push origin "${tag}"
    echo "Tagged, built, and pushed ${tag}."
  else
    echo "Tagged and built ${tag} (no origin remote — nothing pushed)."
  fi

# Clean up build artifacts and temporary files
clean:
  rm -rf build/ dist/ *.egg-info
  find . -type d -name __pycache__ -not -path './.venv/*' -not -path './mcp_server/.venv/*' -exec rm -rf {} +
  rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage

# Run the test suite
test:
  uv run pytest

# Run tests with coverage
test-cov:
  uv run --with pytest-cov pytest --cov=tetris --cov-report=term-missing

# Run interactive pytest
test-watch:
  uv run --with pytest-watch ptw

# Install git pre-commit hooks (mypy + ruff quality gates)
install-hooks:
  uv run --with pre-commit pre-commit install

# Check the code
lint:
  uv run --with ruff ruff check .

# Format the code
fmt:
  uv run --with ruff ruff format .

# Full quality gate: ruff lint (src + tests) + mypy --strict (the package)
check:
  uv run --with ruff ruff check src tests
  uv run --with mypy mypy src/tetris

# Run the Tetris MCP play-test server (stdio; used by the agent)
mcp:
  uv run tetris-vt-server
