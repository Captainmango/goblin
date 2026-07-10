from goblin.utils.tree import walk_strings


def test_transforms_string_leaf():
    assert walk_strings("x", str.upper) == "X"


def test_rebuilds_nested_dict_and_list():
    data = {"a": ["x", {"b": "y"}]}
    assert walk_strings(data, str.upper) == {"a": ["X", {"b": "Y"}]}


def test_dict_keys_untouched():
    assert walk_strings({"key": "val"}, str.upper) == {"key": "VAL"}


def test_non_string_passthrough():
    assert walk_strings(42, str.upper) == 42
    assert walk_strings(None, str.upper) is None
    assert walk_strings(True, str.upper) is True


def test_exception_from_fn_propagates():
    def _raise(s):
        raise ValueError(s)

    import pytest

    with pytest.raises(ValueError, match="boom"):
        walk_strings({"a": "boom"}, _raise)
