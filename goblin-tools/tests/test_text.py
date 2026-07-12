from goblin_tools.text import slugify


def test_slugify() -> None:
    assert slugify("Hello World") == "hello-world"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"
