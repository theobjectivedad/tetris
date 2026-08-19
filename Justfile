set shell := ["bash", "-cu"]

# Start the game
run:
  uv run python -m tetris.main

# Build a distribution (wheel + sdist) into dist/
build:
  uv build

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

# Check the code
lint:
  uv run --with ruff ruff check .

# Format the code
fmt:
  uv run --with ruff ruff format .

# Run the Tetris MCP play-test server (stdio; used by the agent)
mcp:
  uv run tetris-vt-server
