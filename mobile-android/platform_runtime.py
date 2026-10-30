"""Android-only replacement for runtime paths; never imported by the desktop."""
import os
from pathlib import Path

APP_NAME = 'GeoJardins'

def application_root():
    return Path(os.environ['GEOJARDINS_ANDROID_BASE'])

def resource_path(*parts):
    return application_root().joinpath(*parts)

def local_app_data_dir():
    path = Path(os.environ['GEOJARDINS_ANDROID_FILES'])
    path.mkdir(parents=True, exist_ok=True)
    return path

def user_config_path():
    return local_app_data_dir() / 'preferences.json'

def log_directory():
    path = local_app_data_dir() / 'logs'
    path.mkdir(exist_ok=True)
    return path

def geocoding_cache_path():
    return local_app_data_dir() / 'geocoding_cache.sqlite'
