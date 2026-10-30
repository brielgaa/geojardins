"""Keep the original repository-wide desktop pytest invocation isolated.

Run mobile tests explicitly: pytest mobile-android/tests (uses ../pytest.ini).
The Android runtime has intentionally different paths/dependencies, so mixing
both packages in one interpreter would invalidate either suite.
"""
from pathlib import Path

MOBILE = Path(__file__).resolve().parents[1]

def pytest_ignore_collect(collection_path, config):
    return config.rootpath.resolve() != MOBILE.resolve()
