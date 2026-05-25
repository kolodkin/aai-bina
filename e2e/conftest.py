import os

import pytest
from playwright.sync_api import expect

# Mirror playwright.config.ts: 15s default assertion timeout.
expect.set_options(timeout=15_000)


@pytest.fixture(scope="session")
def base_url() -> str:
    # The app under test is started separately (Vite dev server, or the FastAPI
    # backend serving the built SPA); point at it with BASE_URL.
    return os.environ.get("BASE_URL", "http://localhost:5173")


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    return {**browser_type_launch_args, "args": ["--no-sandbox"]}


@pytest.fixture
def browser_context_args(browser_context_args: dict) -> dict:
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}}
