from luch.firmware import newest_companion, parse_version
from luch.i18n import tr


def test_translation_only_in_english_mode():
    assert tr("СВЯЗЬ", "ru") == "СВЯЗЬ"
    assert tr("СВЯЗЬ", "en") == "LINK"
    assert tr("ПИТАНИЕ ВКЛ · POWER ON", "en") == "POWER ON"
    assert tr("4.01В", "en") == "4.01V"
    assert tr("НЕТ ПОДТВ.", "en") == "NO ACK"


def test_parse_version():
    assert parse_version("v1.17.1-d929643") == (1, 17, 1)
    assert parse_version("companion-v1.18.0") == (1, 18, 0)
    assert parse_version("garbage") is None


def test_newest_companion_ignores_other_firmware_and_prereleases():
    rel = newest_companion([
        {"tag_name": "repeater-v1.19.0", "html_url": "r"},
        {"tag_name": "companion-v1.19.0", "prerelease": True, "html_url": "p"},
        {"tag_name": "companion-v1.18.0", "html_url": "u18"},
        {"tag_name": "companion-v1.17.1", "html_url": "u17"},
    ])
    assert rel.version == (1, 18, 0) and rel.url == "u18"
