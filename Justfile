set shell := ["bash", "-cu"]

# Start the game
run:
  uv run main.py

# Run the test suite
test:
  uv run pytest

# Run tests with coverage
test-cov:
  uv run --with pytest-cov pytest --cov=main --cov-report=term-missing

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
  cd mcp_server && uv run tetris-vt-server
