#!/usr/bin/env python
# cardinal_pythonlib/spreadsheets.py

"""
===============================================================================

    Original code copyright (C) 2009-2021 Rudolf Cardinal (rudolf@pobox.com).

    This file is part of cardinal_pythonlib.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

===============================================================================

**Manipulate spreadsheets.**

Note:

- openpyxl is dreadfully slow. Its results are picklable, but not sensibly so
  (e.g. generating a >500Mb picklefile from a 12Mb spreadsheet.
- xlrd is much faster, but we can't pickle its results.

"""

import datetime
import decimal
from decimal import Decimal
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cardinal_pythonlib.progress import ActivityCounter
from cardinal_pythonlib.reprfunc import simple_repr
try:
    # noinspection PyPackageRequirements
    import xlrd
    # noinspection PyPackageRequirements
    from xlrd import Book
    # noinspection PyPackageRequirements
    from xlrd.sheet import Cell
except ImportError:
    raise ImportError("You must install the 'xlrd' package.")

log = logging.getLogger(__name__)


# =============================================================================
# Consistency checks
# =============================================================================

def all_same(items: Iterable[Any]) -> bool:
    """
    Are all the items the same?

    https://stackoverflow.com/questions/3787908/python-determine-if-all-items-of-a-list-are-the-same-item

    ... though we will also allow "no items" to pass the test.
    """  # noqa
    return len(set(items)) <= 1


def check_attr_all_same(items: Sequence[Any],
                        attr: str,
                        id_attr: str = None,
                        fail_if_different: bool = True) -> None:
    """
    Checks if the value of an attribute is the same across a collection of
    items, and takes some action if not.

    Args:
        items:
            Items to check
        attr:
            Name of attribute whose value should be compared across items.
        id_attr:
            If the attributes are not all the same, use the value of this
            attribute from the first item to give some identifying context
            to the failure message.
        fail_if_different:
            If true, raises ``ValueError`` on failure; otherwise, prints a
            warning to the log.
    """
    values = [getattr(item, attr) for item in items]
    if all_same(values):
        return
    first_item = items[0]
    if id_attr:
        identity = f"For {id_attr}={getattr(first_item, id_attr)!r}, "
    else:
        identity = ""
    msg = f"{identity}attribute {attr} is inconsistent: {values}"
    if fail_if_different:
        raise ValueError(msg)
    else:
        log.warning(msg)


def require_attr_all_same(items: Sequence[Any],
                          attr: str,
                          id_attr: str) -> None:
    """
    Raise if the ``attr`` attribute of each item in ``items`` is not the same.
    See :func:`check_attr_all_same`.
    """
    check_attr_all_same(items, attr, id_attr, fail_if_different=True)


def prefer_attr_all_same(items: Sequence[Any],
                         attr: str,
                         id_attr: str) -> None:
    """
    Warn if the ``attr`` attribute of each item in ``items`` is not the same.
    See :func:`check_attr_all_same`.
    """
    check_attr_all_same(items, attr, id_attr, fail_if_different=False)


# =============================================================================
# Spreadsheet operations: xlrd
# =============================================================================

def load_workbook(spreadsheet_filename: str) -> Book:
    """
    Load a workbook.
    """
    # Pickling creates massive files; skip it
    log.info(f"Loading: {spreadsheet_filename!r}...")
    book = xlrd.open_workbook(
        filename=spreadsheet_filename,
        on_demand=True  # may affect rather little, but we can try
    )
    log.info("... done")
    return book


def read_value_row(row: Sequence[Cell], colnum: int) -> Any:
    """
    Retrieves a value from a cell of a spreadsheet, given a row.

    AVOID: slower than index access (see :class:`SheetHolder`,
    :class:`RowHolder`).
    """
    return row[colnum].value


def read_int_row(row: Sequence[Cell], colnum: int) -> Optional[int]:
    """
    Reads an integer from a spreadsheet, given a row.

    AVOID: slower than index access (see :class:`SheetHolder`,
    :class:`RowHolder`).
    """
    v = read_value_row(row, colnum)
    if v is None:
        return None
    return int(v)


# =============================================================================
# Helper functions
# =============================================================================

def none_or_blank_string(x: Any) -> bool:
    """
    Is ``x`` either ``None`` or a string that is empty or contains nothing but
    whitespace?
    """
    if x is None:
        return True
    elif isinstance(x, str) and not x.strip():
        return True
    else:
        return False


# =============================================================================
# SheetHolder
# =============================================================================

class SheetHolder(object):
    """
    Class to read from an Excel spreadsheet.
    """
    SHEET_NAME = ""  # may be overridden
    HEADER_ROW_ZERO_BASED = 0  # 0 is the first row (usually a header row)
    FIRST_DATA_ROW_ZERO_BASED = 1
    NULL_VALUES = [None, ""]

    BOOL_TRUE_VALUES_LOWERCASE = [1, "t", "true", "y", "yes"]
    BOOL_FALSE_VALUES_LOWERCASE = [0, "f", "false", "n", "no"]
    BOOL_UNKNOWN_VALUES_LOWERCASE = [None, "", "?", "not known", "unknown"]

    def __init__(self,
                 book: Book,
                 sheet_name: str = None,
                 debug_max_rows_per_sheet: int = None) -> None:
        self.book = book
        sheet_name = sheet_name or self.SHEET_NAME
        assert sheet_name, "Provide sheet_name or override SHEET_NAME"
        self.sheet = book.sheet_by_name(sheet_name)
        self.checked_headers = {}  # type: Dict[int, str]
        self.debug_max_rows_per_sheet = debug_max_rows_per_sheet

    # -------------------------------------------------------------------------
    # Information
    # -------------------------------------------------------------------------

    @property
    def n_rows(self) -> int:
        """
        Total number of rows.
        """
        return self.sheet.nrows

    @property
    def n_data_rows(self) -> int:
        """
        Total number of data rows (below any header row).
        """
        return self.n_rows - self.FIRST_DATA_ROW_ZERO_BASED

    @property
    def sheet_name(self) -> str:
        """
        Name of the sheet within the workbook (file).
        """
        return self.sheet.name

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def ensure_header(self, col: int, header: str) -> None:
        """
        Ensures that the header is correct for a specified column.
        """
        if col in self.checked_headers:
            # Already checked.
            return
        v = self.read_str(self.HEADER_ROW_ZERO_BASED, col)
        if v != header:
            raise ValueError(f"Bad header for column {col}: should be "
                             f"{header!r}, but was {v!r}")
        self.checked_headers[col] = v

    # -------------------------------------------------------------------------
    # Reading
    # -------------------------------------------------------------------------

    def read_value(self, row: int, col: int, check_header: str = None) -> Any:
        """
        Retrieves a value from a cell of a spreadsheet.
        """
        if check_header is not None:
            self.ensure_header(col, check_header)
        v = self.sheet.cell_value(row, col)
        if v in self.NULL_VALUES:
            return None
        return v

    def read_datetime(self, row: int, col: int,
                      default: datetime.datetime = None,
                      check_header: str = None) \
            -> Optional[datetime.datetime]:
        """
        Reads a datetime from an Excel spreadsheet via xlrd.

        https://stackoverflow.com/questions/32430679/how-to-read-dates-using-xlrd
        """  # noqa
        v = self.read_value(row, col, check_header=check_header)
        if none_or_blank_string(v):
            return default
        try:
            return datetime.datetime(
                *xlrd.xldate_as_tuple(v, self.book.datemode)
            )
        except (TypeError, ValueError):
            raise ValueError(f"Bad date/time: {v!r}")

    def read_date(self, row: int, col: int,
                  default: datetime.date = None,
                  check_header: str = None) -> Optional[datetime.date]:
        """
        Reads a date from an Excel spreadsheet

        https://stackoverflow.com/questions/32430679/how-to-read-dates-using-xlrd
        """
        dt = self.read_datetime(row, col, check_header=check_header)
        if dt:
            return dt.date()
        return default

    def read_int(self, row: int, col: int,
                 default: int = None,
                 check_header: str = None) -> Optional[int]:
        """
        Reads an integer from a spreadsheet.
        """
        v = self.read_value(row, col, check_header=check_header)
        if none_or_blank_string(v):
            return default
        return int(v)

    def read_float(self, row: int, col: int,
                   default: float = None,
                   check_header: str = None) -> Optional[float]:
        """
        Reads a float from the spreadsheet.
        """
        v = self.read_value(row, col, check_header=check_header)
        if none_or_blank_string(v):
            return default
        return float(v)

    def read_decimal(
            self,
            row: int,
            col: int,
            default: Decimal = None,
            check_header: str = None,
            dp: int = None,
            rounding: str = decimal.ROUND_HALF_UP) -> Optional[Decimal]:
        """
        Reads a Decimal from the spreadsheet.

        If ``dp`` is not ``None``, force the result to a specified number of
        decimal places, using the specified rounding method.
        """
        v = self.read_value(row, col, check_header=check_header)
        if none_or_blank_string(v):
            return default
        x = Decimal(str(v))
        # ... better than Decimal(v), which converts e.g. 7.4 to
        # Decimal('7.4000000000000003552713678800500929355621337890625')
        if dp is not None:
            nplaces = Decimal(10) ** (-dp)
            x = x.quantize(exp=nplaces, rounding=rounding)
        return x

    def read_str(self, row: int, col: int,
                 default: str = None,
                 check_header: str = None) -> Optional[str]:
        """
        Reads a string from a spreadsheet.
        """
        v = self.read_value(row, col, check_header=check_header)
        if none_or_blank_string(v):
            return default
        return str(v).strip()

    def read_str_int(self, row: int, col: int,
                     default: str = None,
                     check_header: str = None) -> Optional[str]:
        """
        Reads a string version of an integer. (This prevents e.g. "2" being
        read as a floating-point value of "2.0" then converted to a string.)
        """
        v_int = self.read_int(row, col, check_header=check_header)
        if v_int is None:
            return default
        return str(v_int)

    def read_bool(self,
                  row: int,
                  col: int,
                  default: bool = None,
                  true_values_lowercase: List[Any] = None,
                  false_values_lowercase: List[Any] = None,
                  unknown_values_lowercase: List[Any] = None,
                  check_header: str = None) \
            -> Optional[bool]:
        """
        Reads a boolean value.
        """
        if true_values_lowercase is None:
            true_values_lowercase = self.BOOL_TRUE_VALUES_LOWERCASE
        if false_values_lowercase is None:
            false_values_lowercase = self.BOOL_FALSE_VALUES_LOWERCASE
        if unknown_values_lowercase is None:
            unknown_values_lowercase = self.BOOL_UNKNOWN_VALUES_LOWERCASE
        raw_v = self.read_value(row, col, check_header=check_header)
        if none_or_blank_string(raw_v):
            v = None
        else:
            try:
                v = int(raw_v)
            except (TypeError, ValueError):
                v = str(raw_v).lower()
        if v in true_values_lowercase:
            return True
        elif v in false_values_lowercase:
            return False
        elif v in unknown_values_lowercase:
            return default
        else:
            raise ValueError(f"Bad boolean value: {raw_v!r}")

    # -------------------------------------------------------------------------
    # Row generators
    # -------------------------------------------------------------------------

    def _setup_for_gen(self, with_counter: bool = True) \
            -> Tuple[int, Optional["ActivityCounter"]]:
        n_rows = self.sheet.nrows
        if with_counter:
            counter = ActivityCounter(
                activity="Reading row", n_total=self.n_data_rows)
        else:
            counter = None
        if self.debug_max_rows_per_sheet is not None:
            log.warning(
                f"Debug option: limiting to "
                f"{self.debug_max_rows_per_sheet} rows from spreadsheet")
            end = min(n_rows, self.debug_max_rows_per_sheet + 1)
        else:
            end = n_rows
        return end, counter

    def gen_row_numbers_excluding_header_row(
            self, with_counter: bool = True) -> Iterable[int]:
        """
        Generates row numbers.

        xlrd uses 0-based numbering, so row 1 is the first beyond a header row.
        """
        end, counter = self._setup_for_gen(with_counter)
        for rownum in range(self.FIRST_DATA_ROW_ZERO_BASED, end):
            if counter is not None:
                counter.tick()
            yield rownum

    def gen_rows_excluding_header_row(
            self, with_counter: bool = True) -> Iterable[Sequence[Cell]]:
        """
        Generates rows. AVOID; index-based access is faster.

        xlrd uses 0-based numbering, so row 1 is the first beyond a header row.
        """
        end, counter = self._setup_for_gen(with_counter)
        for index in range(self.FIRST_DATA_ROW_ZERO_BASED, end):
            if counter is not None:
                counter.tick()
            yield self.sheet.row(index)


# =============================================================================
# RowHolder
# =============================================================================

class RowHolder(object):
    """
    Class to read from a single row of a spreadsheet.

    The intended use is to create something like a dataclass, but one that
    knows its spreadsheet structure. Like this:

    .. code-block:: python

        from cardinal_pythonlib.spreadsheets import RowHolder, SheetHolder

        class ReferralSheetHolder(SheetHolder):
            SHEET_NAME = "Patient Referrals 2018-19"

            def gen_referral_rows(self) -> Iterable["ReferralRow"]:
                for rownum in self.gen_row_numbers_excluding_header_row():
                    yield ReferralRow(self, rownum)

        class ReferralRow(RowHolder):
            def __init__(self, sheetholder: SheetHolder, row: int) -> None:
                super().__init__(sheetholder, row)

                self.inc_next_col()  # column 0: query period; ignore
                self.patient_id = self.str_int_pp()
                self.referral_id_within_patient = self.int_pp()
                self.age_at_referral_int = self.int_pp()
                self.ethnicity = self.str_pp()
                self.gender = self.str_pp(check_header="Gender")

        def import_referrals(book: Book) -> None:
            sheet = ReferralSheetHolder(book)
            for referral in sheet.gen_referral_rows():
                pass  # do something useful here

    """

    def __init__(self, sheetholder: SheetHolder, row: int) -> None:
        self.sheetholder = sheetholder
        self.row = row
        self._next_col = 0

    # -------------------------------------------------------------------------
    # Information
    # -------------------------------------------------------------------------

    @property
    def sheet_name(self) -> str:
        return self.sheetholder.sheet_name

    def _get_relevant_attrs(self) -> List[str]:
        """
        Attributes added by the user, and row number.
        :return:
        """
        avoid = [
            "sheetholder",
            "row",
            "_next_col",
        ]
        user_attrs = [k for k in self.__dict__.keys() if k not in avoid]
        attrs = ["sheet_name", "row"] + sorted(user_attrs)
        return attrs

    def __str__(self) -> str:
        return simple_repr(self, self._get_relevant_attrs())

    # -------------------------------------------------------------------------
    # Read operations, given a column number
    # -------------------------------------------------------------------------
    # Compare equivalents in SheetHolder.

    def read_value(self, col: int, check_header: str = None) -> Any:
        return self.sheetholder.read_value(
            self.row, col, check_header=check_header)

    def read_datetime(self, col: int,
                      default: Any = None,
                      check_header: str = None) -> Optional[datetime.date]:
        return self.sheetholder.read_datetime(
            self.row, col, default, check_header=check_header)

    def read_date(self, col: int,
                  default: Any = None,
                  check_header: str = None) -> Optional[datetime.date]:
        return self.sheetholder.read_date(
            self.row, col, default, check_header=check_header)

    def read_int(self, col: int,
                 default: int = None,
                 check_header: str = None) -> Optional[int]:
        return self.sheetholder.read_int(
            self.row, col, default, check_header=check_header)

    def read_float(self, col: int,
                   default: float = None,
                   check_header: str = None) -> Optional[float]:
        return self.sheetholder.read_float(
            self.row, col, default, check_header=check_header)

    def read_decimal(self, col: int,
                     default: Decimal = None,
                     check_header: str = None) -> Optional[Decimal]:
        return self.sheetholder.read_decimal(
            self.row, col, default, check_header=check_header)

    def read_str(self, col: int,
                 default: str = None,
                 check_header: str = None) -> Optional[str]:
        return self.sheetholder.read_str(
            self.row, col, default, check_header=check_header)

    def read_str_int(self, col: int,
                     default: str = None,
                     check_header: str = None) -> Optional[str]:
        return self.sheetholder.read_str_int(
            self.row, col, default, check_header=check_header)

    def read_bool(self,
                  col: int,
                  default: bool = None,
                  true_values_lowercase: List[Any] = None,
                  false_values_lowercase: List[Any] = None,
                  unknown_values_lowercase: List[Any] = None,
                  check_header: str = None) -> Optional[bool]:
        return self.sheetholder.read_bool(
            row=self.row,
            col=col,
            default=default,
            true_values_lowercase=true_values_lowercase,
            false_values_lowercase=false_values_lowercase,
            unknown_values_lowercase=unknown_values_lowercase,
            check_header=check_header
        )

    # -------------------------------------------------------------------------
    # Read operations, incrementing the next column number automatically.
    # -------------------------------------------------------------------------
    # "pp" for "++" post-increment, like C.

    @property
    def next_col(self) -> int:
        """
        Returns the column number (0-based) that will be used by the next
        automatic read operation.
        """
        return self._next_col

    def set_next_col(self, col: int) -> None:
        """
        Resets the next column to be read automatically.
        """
        self._next_col = col

    def inc_next_col(self) -> None:
        """
        Increments the next column to be read.
        """
        self._next_col += 1

    def value_pp(self, check_header: str = None) -> Any:
        """
        Reads a value, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_value(self._next_col, check_header=check_header)
        self.inc_next_col()
        return v

    def datetime_pp(self,
                    default: datetime.datetime = None,
                    check_header: str = None) -> Optional[datetime.datetime]:
        """
        Reads a datetime, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_datetime(
            self._next_col, default, check_header=check_header)
        self.inc_next_col()
        return v

    def date_pp(self,
                default: datetime.date = None,
                check_header: str = None) -> Optional[datetime.date]:
        """
        Reads a date, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_date(self._next_col, default, check_header=check_header)
        self.inc_next_col()
        return v

    def int_pp(self,
               default: int = None,
               check_header: str = None) -> Optional[int]:
        """
        Reads an int, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_int(self._next_col, default, check_header=check_header)
        self.inc_next_col()
        return v

    def float_pp(self,
                 default: float = None,
                 check_header: str = None) -> Optional[float]:
        """
        Reads a float, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_float(self._next_col, default, check_header=check_header)
        self.inc_next_col()
        return v

    def decimal_pp(self,
                   default: float = None,
                   check_header: str = None,
                   dp: int = None,
                   rounding: str = decimal.ROUND_HALF_UP) -> Optional[Decimal]:
        """
        Reads a Decimal, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_decimal(
            self._next_col, default, check_header=check_header)
        self.inc_next_col()
        return v

    def str_pp(self,
               default: str = None,
               check_header: str = None) -> Optional[str]:
        """
        Reads a string, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_str(self._next_col, default, check_header=check_header)
        self.inc_next_col()
        return v

    def str_int_pp(self,
                   default: str = None,
                   check_header: str = None) -> Optional[str]:
        """
        Reads an integer as a string, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_str_int(
            self._next_col, default, check_header=check_header)
        self.inc_next_col()
        return v

    def bool_pp(self,
                default: bool = None,
                true_values_lowercase: List[Any] = None,
                false_values_lowercase: List[Any] = None,
                unknown_values_lowercase: List[Any] = None,
                check_header: str = None) -> Optional[bool]:
        """
        Reads a boolean value, then increments the "current" column.
        Optionally, checks that the header for this column is as expected.
        """
        v = self.read_bool(
            col=self._next_col,
            default=default,
            true_values_lowercase=true_values_lowercase,
            false_values_lowercase=false_values_lowercase,
            unknown_values_lowercase=unknown_values_lowercase,
            check_header=check_header
        )
        self.inc_next_col()
        return v
