from luch.app import LuchApp, radio_slug
from luch.radio import RadioState, gps_from_lpp
from luch.weather import parse


def test_gps_from_lpp():
    lpp = [{"channel": 1, "type": "voltage", "value": 4.1},
           {"channel": 1, "type": "gps", "value": {"latitude": 33.6, "longitude": -117.7, "altitude": 120.0}}]
    assert gps_from_lpp(lpp) == (33.6, -117.7, 120.0)
    assert gps_from_lpp([{"type": "gps", "value": {"latitude": 0, "longitude": 0}}]) is None  # no fix


def test_weather_parse():
    w = parse({"current": {"temperature_2m": 62.4, "apparent_temperature": 60.0, "relative_humidity_2m": 41,
                           "weather_code": 0, "wind_speed_10m": 5.2, "wind_direction_10m": 315}}, True)
    assert (w.summary_ru, w.compass, w.temp_unit, w.wind_unit) == ("ЯСНО", "NW", "°F", "mph")


def test_radio_slug():
    assert radio_slug("MeshCore-Mojeska") == "mojeska"
    assert radio_slug("Wio Tracker #2") == "wio-tracker-2"


async def test_position_prefers_radio_over_home(tmp_path):
    app = LuchApp("x", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(140, 44)):
        assert app.position() is None
        app.settings["home"] = [33.6, -117.7]
        assert app.position() == (33.6, -117.7, "ДОМ")
        app.state = RadioState(connected=True, gps=True, lat=34.0, lon=-118.0, pos_src="GPS")
        assert app.position() == (34.0, -118.0, "GPS")


async def test_home_without_args_reports(tmp_path):
    app = LuchApp("x", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press(*"/home", "enter")
        await pilot.pause()
        assert "home" not in app.settings  # just reported, nothing set
