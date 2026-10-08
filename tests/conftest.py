import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Keep the settings singleton from leaking between tests."""
    get_settings.cache_clear()
