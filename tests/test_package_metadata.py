from importlib.resources import files


def test_package_declares_inline_typing_support():
    assert files("backlog_py").joinpath("py.typed").is_file()
