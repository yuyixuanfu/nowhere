# nowhere/tests/test_terrain.py
from nowhere import terrain


def test_everest_high():
    assert terrain.elevation(27.9881, 86.9250) > 7000


def test_dead_sea_below_sea_level():
    assert terrain.elevation(31.5, 35.5) < -300


def test_ocean_is_water():
    assert terrain.is_water(0.0, -30.0) is True
    assert terrain.is_water(39.9, 116.4) is False


def test_slope_uphill():
    deg, dist = terrain.slope_between((27.9, 86.8), (27.9881, 86.9250))
    assert deg > 5 and dist > 5


def test_destination_moves_north():
    lat, lon = terrain.destination(35.0, 139.0, 0.0, 10.0)
    assert lat > 35.05 and abs(lon - 139.0) < 0.01


def test_surface_sahara_is_sand():
    assert terrain.surface(23.0, 8.0) == "sand"


def test_destination_date_line_east():
    """Crossing the date line eastward should wrap longitude into [-180, 180]."""
    lat, lon = terrain.destination(0, 179.9, 90, 50)
    assert -180 <= lon <= 180
    assert lon < 0  # crossed the date line → negative


def test_destination_date_line_west():
    """Crossing the date line westward should wrap longitude into [-180, 180]."""
    lat, lon = terrain.destination(0, -179.9, 270, 50)
    assert -180 <= lon <= 180
    assert lon > 0  # crossed the date line → positive


def test_no_network(monkeypatch):
    import socket
    monkeypatch.setattr(
        socket.socket, "connect",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")),
    )
    assert terrain.elevation(27.9881, 86.9250) > 7000
