"""What Napier fills in on the BASIC INFO sheet.

B3 is the clinic date, and it is not a label: the twenty year cut on the SOL
sheet, both expungement waits and the has-the-client-turned-18 test all compare
against it. A blank B3 is zero to Excel, so every one of those tests fails and
the workbook reads as a client with nothing stale and nothing expungeable. That
is a wrong answer with no error attached to it, which is worse than a blank.
"""

import datetime
import os
import sys

from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tasks


def _build(is_lite=False):
    """A workbook with no cases in it. BASIC INFO does not depend on them."""
    path, _ = tasks.build_workbook([], 'TEST CLIENT', '01/01/1980', is_lite)
    return load_workbook(path)['BASIC INFO'], path


def test_the_clinic_date_is_filled_in_with_a_real_date():
    sheet, path = _build()
    try:
        value = sheet['B3'].value
        assert value is not None, 'a blank B3 silently zeroes every date test'
        assert not isinstance(value, str), 'text does not do date arithmetic'
        if isinstance(value, datetime.datetime):
            value = value.date()
        assert value == datetime.date.today()
        assert 'YY' in sheet['B3'].number_format.upper()
    finally:
        os.remove(path)


def test_the_lite_template_gets_it_too():
    sheet, path = _build(is_lite=True)
    try:
        assert sheet['B3'].value is not None
    finally:
        os.remove(path)


def test_the_client_still_lands_in_the_cells_below_it():
    sheet, path = _build()
    try:
        assert sheet['B5'].value == 'TEST CLIENT'
        assert sheet['B6'].value == '01/01/1980'
    finally:
        os.remove(path)
