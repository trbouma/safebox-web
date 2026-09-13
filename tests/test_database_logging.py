import logging

from app.database import run_migrations


def test_migrations_preserve_application_loggers(tmp_path):
    application_logger = logging.getLogger("safebox_web.test.migrations")
    application_logger.disabled = False

    run_migrations(f"sqlite:///{tmp_path / 'database.db'}")

    assert application_logger.disabled is False
