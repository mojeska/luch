"""Russian screen text and its English translation (Ctrl+T switches). Message text is never touched."""
from __future__ import annotations

# Composite "RU · EN" phrases first, so English mode doesn't say things twice.
EN = {
    "ПИТАНИЕ ВКЛ · POWER ON": "POWER ON",
    "НЕТ АДРЕСАТА · NO TARGET": "NO TARGET",
    "── НЕТ ЗАПИСЕЙ · NO TRAFFIC LOGGED ──": "── NO TRAFFIC LOGGED ──",
    "── НЕ НАЙДЕНО · NOT FOUND ──": "── NOT FOUND ──",
    "АППАРАТУРА ЗАСЕКРЕЧЕННОЙ СВЯЗИ": "SECURE COMMUNICATIONS EQUIPMENT",
    "КАНАЛЫ И АБОНЕНТЫ": "CHANNELS AND CONTACTS",
    "ЖУРНАЛ СВЯЗИ": "COMMS LOG",
    "ПЕРЕДАЧА ▸": "TRANSMIT ▸",
    "КОМАНДЫ // COMMANDS": "COMMANDS",
    "АБОНЕНТ НЕ НАЙДЕН": "NO SUCH CONTACT",
    "СОВПАДЕНИЙ": "MATCHES",
    "СБОЙ ПЕРЕДАЧИ": "TRANSMIT FAILED",
    "ДОСТУПНА ПРОШИВКА": "FIRMWARE AVAILABLE",
    "ПРОШИВКА АКТУАЛЬНА": "FIRMWARE UP TO DATE",
    "ПРОВЕРКА ПРОШИВКИ": "FIRMWARE CHECK",
    "ЧЕРЕЗ ЛОГГЕР": "VIA LOGGER",
    "УСТАНОВЛЕНА": "ESTABLISHED",
    "АВТОНОМНО": "OFFLINE",
    "ПЕРЕДАЁТСЯ": "SENDING",
    "ОТПРАВЛЕНО": "SENT",
    "ДОСТАВЛЕНО": "DELIVERED",
    "НЕТ ПОДТВ.": "NO ACK",
    "СОСТОЯНИЕ": "STATUS",
    "СОБЫТИЯ": "EVENTS",
    "АБОНЕНТЫ": "CONTACTS",
    "РЕТРАНСЛ.": "REPEATERS",
    "ПРОШИВКА": "FIRMWARE",
    "НОВАЯ": "NEW",
    "ЗАПИСЕЙ": "LOGGED",
    "ЧАСТОТА": "FREQ",
    "ПОЛОСА": "BW",
    "МОЩН.": "TX PWR",
    "АККУМ.": "BATTERY",
    "КАНАЛЫ": "CHANNELS",
    "СПРАВКА": "HELP",
    "ПЕРЕВОД": "LANGUAGE",
    "ПАНЕЛЬ": "PANEL",
    "КАНАЛ": "CHANNEL",
    "ВЫХОД": "QUIT",
    "ПРИЁМ": "RECEIVED",
    "ПОИСК": "SEARCH",
    "РЕЖИМ": "MODE",
    "СВЯЗЬ": "LINK",
    "УЗЕЛ": "NODE",
    "МЕСТН": "LOCAL",
    "ЛУЧ-М": "LUCH-M",
    "СБОЙ": "FAILED",
    "ПРМ": "RX ",
    "ПРД": "TX ",
    "АБН": "DM",
    "КОМ": "RM",
    "АРХ": "OLD",
    "НЕТ": "DOWN",
    "МГц": "MHz",
    "кГц": "kHz",
    "дБм": "dBm",
    "К-": "C-",
    "В": "V",
}
_KEYS = sorted(EN, key=len, reverse=True)
_SUBTITLES = {"CHANNELS · CONTACTS", "COMMS LOG", "STATUS", "EVENTS"}


def tr(text: str, lang: str) -> str:
    """Translate fixed screen text only; never pass names or message text through this."""
    if lang != "en":
        return text
    for k in _KEYS:
        if k in text:
            text = text.replace(k, EN[k])
    return text


def subtitle(text: str, lang: str) -> str:
    """The small English subtitles under Russian titles are redundant in English mode."""
    return "" if lang == "en" and text in _SUBTITLES else text
