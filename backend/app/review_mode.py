import os


def review_mode_enabled():
    return os.getenv('REVIEW_MODE', '').strip().lower() in {
        '1', 'true', 'yes', 'on'
    }
