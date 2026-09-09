"""
Commas in values that are not thousands separators.

A spreadsheet importer wants "1,234" read as 1234, and stripping every comma
is the shortest way to get there. It is also how a supplier's craftType of
"1,2" -- meaning a style supports both print techniques -- became the integer
twelve, and 34 of 64 styles were stored claiming a craft type that does not
exist. Nothing failed and nothing was logged; the column simply held wrong
numbers, and filtering craftType = 1 for heat-transfer styles quietly missed
every style that also supports DTG.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.schema.registry import DataType
from app.domain.uploads.parser import _as_decimal, _as_int, coerce, infer_type


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------
def test_a_list_of_codes_is_not_a_number():
    assert _as_int("1,2") is None
    assert _as_decimal("1,2") is None


def test_a_column_of_craft_types_stays_text():
    """The supplier's actual values, in the proportion they actually arrive."""
    assert infer_type(["1", "1,2", "2", "1,2"]) is DataType.TEXT


def test_the_value_survives_as_what_it_was():
    assert coerce("1,2", DataType.TEXT) == "1,2"


def test_one_comma_value_makes_the_whole_column_text():
    """
    Narrowest-that-fits, applied honestly: 63 clean integers and one "1,2"
    must not produce an integer column that drops or mangles the odd one.
    """
    assert infer_type(["1"] * 63 + ["1,2"]) is DataType.TEXT


# ---------------------------------------------------------------------------
# Without breaking what the rule is for
# ---------------------------------------------------------------------------
def test_real_thousands_separators_still_parse():
    assert _as_int("1,234") == 1234
    assert _as_int("12,345,678") == 12345678
    assert _as_decimal("1,234.56") == Decimal("1234.56")
    assert infer_type(["1,234", "5,678"]) is DataType.INTEGER


def test_plain_numbers_are_untouched():
    assert _as_int("42") == 42
    assert _as_int("-7") == -7
    assert _as_decimal("3.14") == Decimal("3.14")
    assert _as_decimal("$1,999.00") == Decimal("1999.00")


def test_groups_that_are_not_groups_of_three_are_refused():
    """
    The distinction the whole fix rests on: a thousands separator is followed
    by exactly three digits, every time.
    """
    for value in ("1,23", "1,2345", "12,34", "1,2,3"):
        assert _as_int(value) is None, value
        assert _as_decimal(value) is None, value
