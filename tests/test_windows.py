from mediaqc.core.analyzer.windows import window_positions_seconds


def test_window_positions_zero_duration():
    assert window_positions_seconds(0) == [0.0, 0.0, 0.0, 0.0, 0.0]


def test_window_positions_percentages_for_long_episode():
    # 24 min (1440s): el 2% (28.8s) cae bajo el piso de 60s y se clampea;
    # el resto cae bien adentro del margen de los bordes.
    positions = window_positions_seconds(1440)
    assert positions == [
        60.0,
        0.25 * 1440,
        0.50 * 1440,
        0.75 * 1440,
        0.95 * 1440,
    ]


def test_window_positions_clamped_near_edges_for_short_episode():
    # episodio de 100s: no hay margen de 60s en cada punta, así que no se
    # aplica el clamp (lo/hi quedan en 0/duration).
    positions = window_positions_seconds(100)
    assert positions[0] == 0.02 * 100
    assert positions[-1] == 0.95 * 100


def test_window_positions_clamps_first_and_last_minute_for_long_episode():
    # 200s (>150s): el 2% (4s) queda bajo el piso de 60s y se clampea a 60;
    # el 95% (190s) queda por encima del techo (200-60=140) y se clampea ahí.
    positions = window_positions_seconds(200)
    assert positions[0] == 60.0
    assert positions[-1] == 140.0


def test_window_positions_returns_five_values():
    assert len(window_positions_seconds(600)) == 5
