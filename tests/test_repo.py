from types import SimpleNamespace

from mediaqc.core.db.repo import episode_runtime_deviates


def test_runtime_deviates_true_for_truncated_file():
    ep = SimpleNamespace(duration_s=600, tmdb_runtime_min=24)  # 10 min real vs 24 min oficial
    assert episode_runtime_deviates(ep) is True


def test_runtime_deviates_false_within_threshold():
    ep = SimpleNamespace(duration_s=23 * 60, tmdb_runtime_min=24)  # ~4% de diferencia
    assert episode_runtime_deviates(ep) is False


def test_runtime_deviates_false_when_duration_missing():
    ep = SimpleNamespace(duration_s=None, tmdb_runtime_min=24)
    assert episode_runtime_deviates(ep) is False


def test_runtime_deviates_false_when_tmdb_runtime_missing():
    ep = SimpleNamespace(duration_s=600, tmdb_runtime_min=None)
    assert episode_runtime_deviates(ep) is False


def test_runtime_deviates_false_when_tmdb_runtime_zero():
    ep = SimpleNamespace(duration_s=600, tmdb_runtime_min=0)
    assert episode_runtime_deviates(ep) is False
