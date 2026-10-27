"""A news-ticker scrolling along the bottom of the screen: historical Soviet slogans and Ukrainian ones."""
from __future__ import annotations

import random

from rich.text import Text
from textual.widgets import Static

# Real slogans from posters, speeches and songs, with English translations.
SLOGANS = [
    ("ПРОЛЕТАРИИ ВСЕХ СТРАН, СОЕДИНЯЙТЕСЬ!", "Workers of the world, unite!"),
    ("НЕ БОЛТАЙ!", "Don't chatter! (1941 poster)"),
    ("БОЛТУН — НАХОДКА ДЛЯ ШПИОНА", "A chatterbox is a gift to a spy"),
    ("ПЯТИЛЕТКУ — В ЧЕТЫРЕ ГОДА!", "The five-year plan in four years!"),
    ("ВСЯ ВЛАСТЬ СОВЕТАМ!", "All power to the Soviets!"),
    ("МИР! ТРУД! МАЙ!", "Peace! Labour! May!"),
    ("СЛАВА ТРУДУ!", "Glory to labour!"),
    ("ДОГОНИМ И ПЕРЕГОНИМ АМЕРИКУ!", "Catch up with and overtake America!"),
    ("КОММУНИЗМ ЕСТЬ СОВЕТСКАЯ ВЛАСТЬ ПЛЮС ЭЛЕКТРИФИКАЦИЯ ВСЕЙ СТРАНЫ",
     "Communism is Soviet power plus the electrification of the whole country (Lenin)"),
    ("РОДИНА-МАТЬ ЗОВЁТ!", "The Motherland calls! (1941 poster)"),
    ("МЫ НАШ, МЫ НОВЫЙ МИР ПОСТРОИМ!", "We will build our own new world! (The Internationale)"),
    ("НАРОД И ПАРТИЯ ЕДИНЫ!", "The people and the Party are one!"),
    ("СЛАВА СОВЕТСКИМ КОСМОНАВТАМ!", "Glory to Soviet cosmonauts!"),
    ("ВПЕРЁД, К ПОБЕДЕ КОММУНИЗМА!", "Forward to the victory of communism!"),
    ("ХЛЕБ — ВСЕМУ ГОЛОВА!", "Bread is the head of everything!"),
    ("КНИГА — ЛУЧШИЙ ПОДАРОК!", "A book is the best gift!"),
    ("ЗДОРОВЬЕ В ПОРЯДКЕ — СПАСИБО ЗАРЯДКЕ!", "Healthy and fit, thanks to the morning workout!"),
    ("ЛЕНИН ЖИЛ, ЛЕНИН ЖИВ, ЛЕНИН БУДЕТ ЖИТЬ!", "Lenin lived, Lenin lives, Lenin will live!"),
    ("УЧИТЬСЯ, УЧИТЬСЯ И УЧИТЬСЯ!", "Study, study and study! (Lenin)"),
    ("ПАРТИЯ — НАШ РУЛЕВОЙ!", "The Party is our helmsman!"),
]
# Ukrainian slogans, shown in Ukrainian-flag yellow.
UA_SLOGANS = [
    ("СЛАВА УКРАЇНІ! — ГЕРОЯМ СЛАВА!", "Glory to Ukraine! Glory to the heroes!"),
    ("ВСЕ БУДЕ УКРАЇНА!", "Everything will be Ukraine!"),
    ("УКРАЇНА ПОНАД УСЕ!", "Ukraine above all!"),
    ("СЛАВА ЗСУ!", "Glory to the Armed Forces of Ukraine!"),
    ("РАЗОМ ДО ПЕРЕМОГИ!", "Together toward victory!"),
    ("БУДЬМО!", "Let us be! (a toast)"),
    ("ТРИМАЙСЯ!", "Hang in there! Stay strong!"),
    ("ПОВЕРТАЙСЯ ЖИВИМ І ЗДОРОВЕНЬКИМ", "Come back alive and well"),
]
SEP = "   ★   "


class Ticker(Static):
    """Scrolls one character every `speed` seconds; reshuffles after each full pass."""

    speed = 0.12

    def on_mount(self) -> None:
        self._offset = 0
        self._tape = self._shuffle()
        self._next = self._shuffle()
        self.set_interval(self.speed, self._step)
        self.call_after_refresh(self._draw_tape)  # show text straight away, not after the first step

    def _shuffle(self) -> Text:
        lang = getattr(self.app, "lang", "ru")
        t = Text()
        pool = [(ru, en, False) for ru, en in SLOGANS] + [(ua, en, True) for ua, en in UA_SLOGANS]
        for text, en, ukrainian in random.sample(pool, len(pool)):
            colour = "bold #ffd700" if ukrainian else "bold #ffe9a8"
            if lang == "en":
                t.append(en.upper(), style=colour)
            else:
                t.append(text, style=colour)
                t.append(f"  ({en})", style="#e8a598")
            t.append(SEP, style="#ffb000")
        return t

    def rebuild(self) -> None:
        """Start over, e.g. in the other language."""
        self._offset = 0
        self._tape, self._next = self._shuffle(), self._shuffle()
        self._draw_tape()

    def _step(self) -> None:
        if not self.display:
            return
        self._offset += 1
        if self._offset >= len(self._tape):  # the next shuffle is already on screen: no jump
            self._offset -= len(self._tape)
            self._tape, self._next = self._next, self._shuffle()
        self._draw_tape()

    def _draw_tape(self) -> None:
        width = max(1, self.size.width)
        view = self._tape + self._next
        self.update(view[self._offset:self._offset + width])
