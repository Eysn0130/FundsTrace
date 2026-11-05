"""Pure text representation must not replace typed source cells."""
import datetime as dt
from decimal import Decimal

import pytest

from app import table_import as table


@pytest.mark.parametrize("value,expected", [
    (None, ""), ("", ""), (" 000123 ", "000123"), (" NULL ", "NULL"),
    ("\u00a0银行\u00a0", "银行"), ("A\u200bB", "A\u200bB"), ("A\nB", "A\nB"),
    (123, "123"), (True, "True"), (1.25, "1.25"), (Decimal("1.2300"), "1.2300"),
    (dt.datetime(2026, 1, 2, 3, 4, 5, 123456), "2026-01-02 03:04:05.123456"),
    (dt.date(2026, 1, 2), "2026-01-02"), (dt.time(3, 4, 5, 123456), "03:04:05.123456"),
])
def test_text_preserves_source_type_and_exact_representation(value, expected):
    assert table._text(value) == expected
    wrapped = table._WorkbookValue(value, number_format="000000", formula=True, date_only=True, invalid_type=True)
    assert table._text(wrapped) == expected
    assert table._raw(wrapped) is value
    assert (wrapped.number_format, wrapped.formula, wrapped.date_only, wrapped.invalid_type) == ("000000", True, True, True)


def test_string_subclass_keeps_string_conversion_instead_of_custom_strip():
    class SourceString(str):
        def __str__(self):
            return " 000456 "

        def strip(self, *args):
            raise AssertionError("source subclass strip is not the text conversion contract")

    value = SourceString("original")
    assert table._text(value) == "000456"
    assert table._text(table._WorkbookValue(value)) == "000456"
