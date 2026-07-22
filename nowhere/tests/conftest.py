import pytest
from nowhere import geocode, providers


@pytest.fixture(autouse=True)
def _reset_providers():
    providers.reset_for_tests()
    geocode.clear_cache()
