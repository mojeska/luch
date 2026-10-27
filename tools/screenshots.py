"""Render the README / website screenshots as SVG with made-up demo data (no real messages or places).

    LUCH_SOUND=off .venv/bin/python tools/screenshots.py docs/
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

from luch import firmware
from luch.app import LuchApp
from luch.mascot import Mascot
from luch.radio import Channel, Contact, RadioState
from luch.weather import Weather

BAIKONUR = (45.9646, 63.3052)
CHAT = [
    ("in", "Gagarin-1", "Поехали! Signal strong from the launch pad", 6.5, 2),
    ("in", "K6XYZ", "Copy, 3 hops out. Good evening, comrades", -4.25, 3),
    ("out", "Zarya", "Слышу хорошо. Testing ЛУЧ on the new firmware", None, None),
    ("in", "Valentina", "anyone up for a mesh test tonight?", 1.0, -1),
    ("in", "Gagarin-1", "Weather at the pad: clear, wind light", 7.25, 2),
]


async def shoot(out: Path, lang: str, name: str, countdown: bool = False) -> None:
    db = Path(tempfile.mkdtemp()) / "demo.db"
    app = LuchApp("demo", db, offline=True)
    app.lang = lang
    async with app.run_test(size=(150, 46)) as pilot:
        app.offline = False
        app.via_logger = True
        state = RadioState(
            connected=True, name="Zarya", link="WIFI", freq=927.875, bw=62.5, sf=7, tx_power=22,
            battery_mv=4012, firmware="v1.18.0-81dbae5", gps=True, gps_interval=60,
            lat=BAIKONUR[0], lon=BAIKONUR[1], alt=90.0, pos_src="GPS",
            channels=[Channel(0, "Public"), Channel(1, "#test"), Channel(2, "#3dprinting"), Channel(3, "#cosmos")],
            contacts=[Contact("ab" * 32, "Gagarin-1", 1, 0, 2, 1), Contact("cd" * 32, "Valentina", 1, 0, 1),
                      Contact("ef" * 32, "Baikonur Room", 3, 0, 1), Contact("12" * 32, "Hilltop RPT", 2, 0, 1)],
        )
        app.fw_latest = firmware.Release((1, 18, 0), "companion-v1.18.0", "")
        app._fw_for = state.firmware
        app.weather = Weather(temp=58, feels=55, humidity=31, wind=7, wind_dir=300, code=0, imperial=True,
                              fetched=time.time())
        app.on_radio("state", state)
        t = int(time.time()) - 600
        for i, (d, who, text, snr, hops) in enumerate(CHAT):
            mid = app.store.add("chan:0", d, who, text, t + i * 60, snr=snr, path_len=hops,
                                status="delivered" if d == "out" else "")
            app.on_radio("message", mid)
        app.on_radio("message", app.store.add("chan:3", "in", "Valentina", "Чайка here", t + 400, snr=3, path_len=1))
        app.on_radio("status", "LINK ESTABLISHED")
        app.event("ПУСК ", free="T-10") if countdown else None
        app.render_status()
        if countdown:
            app.sound.player = ["true"]
            app.action_launch()
            app._count = 7
            app._draw_count()
        else:
            app.query_one(Mascot).react("wave", 6)
        await pilot.pause(0.6)
        app.save_screenshot(str(out / f"{name}.svg"))
        app.sound.stop_anthem()


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    await shoot(out, "ru", "screenshot")
    await shoot(out, "en", "screenshot-en")
    await shoot(out, "ru", "countdown", countdown=True)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs")))
