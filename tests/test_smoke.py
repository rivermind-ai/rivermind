import rivermind


def test_version_is_set() -> None:
    assert isinstance(rivermind.__version__, str)
    assert rivermind.__version__
