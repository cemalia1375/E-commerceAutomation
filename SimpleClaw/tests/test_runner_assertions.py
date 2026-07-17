from script.runner.assertions import evaluate_checks


def test_tool_called_count_passes_for_exact_count() -> None:
    results = evaluate_checks(
        [{"tool_called_count": "device_command:1"}],
        {"tools_called": ["device_command", "analyze_image"]},
    )

    assert results[0].passed is True
    assert "device_command_count=1" in results[0].detail


def test_tool_called_count_fails_for_repeated_tool() -> None:
    results = evaluate_checks(
        [{"tool_called_count": "device_command:1"}],
        {"tools_called": ["device_command", "analyze_image", "device_command"]},
    )

    assert results[0].passed is False
    assert "device_command_count=2" in results[0].detail


def test_tool_called_count_accepts_dict_format() -> None:
    results = evaluate_checks(
        [{"tool_called_count": {"tool": "analyze_image", "count": 2}}],
        {"tools_called": ["analyze_image", "device_command", "analyze_image"]},
    )

    assert results[0].passed is True
