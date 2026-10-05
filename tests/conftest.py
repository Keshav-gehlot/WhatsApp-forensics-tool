import pytest
from modules import custody


@pytest.fixture(autouse=True)
def _reset_custody():
    custody.set_log_folder(None)
    yield
    custody.set_log_folder(None)
