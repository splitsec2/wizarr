"""The audit trail is also written to a rotating file that survives a container recreate."""

import logging
import logging.config

import pytest

from app import logging_config


@pytest.fixture
def pristine_config(monkeypatch):
    config = logging_config.LOGGING_CONFIG
    fresh = {
        **config,
        "handlers": dict(config["handlers"]),
        "loggers": {
            name: {**logger, "handlers": list(logger["handlers"])}
            for name, logger in config["loggers"].items()
        },
    }
    monkeypatch.setattr(logging_config, "LOGGING_CONFIG", fresh)


def test_audit_lines_reach_the_file(tmp_path, monkeypatch, pristine_config):
    target = tmp_path / "audit" / "audit.log"
    monkeypatch.setenv("WIZARR_AUDIT_LOG", str(target))

    logging_config.configure_logging()
    try:
        logging.getLogger("wizarr.audit").info("disable server=7 result=ok")
        for handler in logging.getLogger("wizarr.audit").handlers:
            handler.flush()
    finally:
        for handler in list(logging.getLogger("wizarr.audit").handlers):
            if handler.__class__.__name__ == "RotatingFileHandler":
                handler.close()
                logging.getLogger("wizarr.audit").removeHandler(handler)

    assert "disable server=7 result=ok" in target.read_text()


def test_off_adds_no_file_handler(monkeypatch, pristine_config):
    monkeypatch.setenv("WIZARR_AUDIT_LOG", "off")

    logging_config._add_audit_file()

    assert "audit_file" not in logging_config.LOGGING_CONFIG["handlers"]


def test_an_unwritable_path_is_skipped_not_fatal(
    tmp_path, monkeypatch, capsys, pristine_config
):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("WIZARR_AUDIT_LOG", str(blocker / "audit.log"))

    logging_config._add_audit_file()

    assert "audit_file" not in logging_config.LOGGING_CONFIG["handlers"]
    assert "not writable" in capsys.readouterr().err
