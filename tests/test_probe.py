from mediaqc.core.probe import extract_container_delay_ms


def test_container_delay_zero_when_no_data():
    assert extract_container_delay_ms({}) == 0


def test_container_delay_positive_from_tag():
    assert extract_container_delay_ms({"tags": {"DELAY": "250"}}) == 250


def test_container_delay_negative_from_tag():
    assert extract_container_delay_ms({"tags": {"DELAY": "-120"}}) == -120


def test_container_delay_from_start_time_positive():
    assert extract_container_delay_ms({"start_time": "0.500"}) == 500


def test_container_delay_from_start_time_negative():
    assert extract_container_delay_ms({"start_time": "-0.033"}) == -33


def test_container_delay_tag_takes_precedence_over_start_time():
    stream = {"start_time": "1.0", "tags": {"delay": "10"}}
    assert extract_container_delay_ms(stream) == 10
