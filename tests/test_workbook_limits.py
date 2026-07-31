"""The CRS template's own limits, read back out of the template.

Napier writes CASE DATA rows until it runs out of cases. The workbook it writes
them into stops counting at some point on every sheet, and stops having rows at
all a little further down, and Excel reports neither. The numbers in crs.py are
what the finish page warns on, so they are checked against the two .xlsx files
here rather than trusted: a CRS 3.6 that moves them fails this instead of
quietly making the warning wrong.
"""

import os
import re
import sys

import pytest
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, 'CRS 3.5.5.xlsx')
LITE = os.path.join(ROOT, 'CRS Lite 3.5.5.xlsx')

# The three sheets whose row-1 totals are the ones staff read off.
TOTALLED = ('SOL', 'BANKRUPTCY', 'EXEMPTIONS')


def _total_range_end(sheet):
    """Last row the =SUM() totals on row 1 reach, or None if there are none."""
    for cell in sheet[1]:
        if isinstance(cell.value, str) and cell.value.startswith('=SUM('):
            match = re.search(r':[A-Z]+(\d+)\)', cell.value.replace('\n', ''))
            if match:
                return int(match.group(1))
    return None


def _last_case_row_covered(sheet):
    """Last CASE DATA row this sheet has a row for.

    Each analysis sheet mirrors column A of CASE DATA, some of them offset by a
    row or two, so the reference inside the formula is the honest answer rather
    than the sheet's own row number.
    """
    last = None
    for row in range(1, sheet.max_row + 1):
        value = sheet.cell(row=row, column=1).value
        if not isinstance(value, str):
            continue
        match = re.search(r"'CASE DATA'!A(\d+)", value)
        if match:
            last = max(last or 0, int(match.group(1)))
    return last


@pytest.fixture(scope='module')
def full():
    return load_workbook(FULL)


@pytest.fixture(scope='module')
def lite():
    return load_workbook(LITE)


def test_case_data_totals_stop_where_we_say_they_do(full, lite):
    for workbook in (full, lite):
        end = _total_range_end(workbook['CASE DATA'])
        cases = end - crs.FIRST_CASE_ROW + 1
        assert cases == crs.CASE_DATA_TOTAL_LIMIT


def test_analysis_totals_stop_where_we_say_they_do(full):
    ends = {name: _total_range_end(full[name]) for name in TOTALLED}
    assert None not in ends.values(), ends
    cases = min(ends.values()) - crs.FIRST_CASE_ROW + 1
    assert cases == crs.ANALYSIS_TOTAL_LIMIT


def test_the_lite_template_has_no_totalled_analysis_sheets(lite):
    # The Lite warning skips the totals line, which is only right while this is.
    assert not [name for name in TOTALLED if name in lite.sheetnames]


def test_the_analysis_sheets_run_out_of_rows_where_we_say_they_do(full, lite):
    for workbook, limit in ((full, crs.ANALYSIS_ROW_LIMIT),
                            (lite, crs.LITE_ANALYSIS_ROW_LIMIT)):
        covered = {}
        for name in workbook.sheetnames:
            if name == 'CASE DATA':
                continue
            last = _last_case_row_covered(workbook[name])
            if last is not None:
                covered[name] = last - crs.FIRST_CASE_ROW + 1
        assert covered, 'no sheet mirrors CASE DATA any more'
        assert min(covered.values()) == limit, covered


def test_a_normal_run_says_nothing():
    assert crs.workbook_limits(70, False) == []
    assert crs.workbook_limits(70, True) == []


def test_the_totals_warning_comes_first_and_only_for_the_full_template():
    full = crs.workbook_limits(crs.ANALYSIS_TOTAL_LIMIT + 1, False)
    assert len(full) == 1
    assert 'SOL, BANKRUPTCY and EXEMPTIONS' in full[0]
    assert crs.workbook_limits(crs.ANALYSIS_TOTAL_LIMIT + 1, True) == []


def test_running_off_the_end_of_everything_says_so():
    warnings = crs.workbook_limits(crs.CASE_DATA_TOTAL_LIMIT + 1, False)
    assert len(warnings) == 3
    assert 'CASE DATA' in warnings[0]
