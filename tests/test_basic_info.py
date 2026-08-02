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

import crs
import tasks


def _build(is_lite=False):
    """A workbook with no cases in it. BASIC INFO does not depend on them."""
    path, _, _ = tasks.build_workbook([], 'TEST CLIENT', '01/01/1980', is_lite)
    return load_workbook(path)['BASIC INFO'], path


def test_the_clinic_date_is_filled_in_with_a_real_date():
    sheet, path = _build()
    try:
        value = sheet['B3'].value
        assert value is not None, 'a blank B3 silently zeroes every date test'
        assert not isinstance(value, str), 'text does not do date arithmetic'
        if isinstance(value, datetime.datetime):
            value = value.date()
        assert value == crs.iowa_today()
        assert 'YY' in sheet['B3'].number_format.upper()
    finally:
        os.remove(path)


class TestTheDayItPutsDown:
    """Whose day it is.

    Napier runs on a dyno that keeps UTC and serves clinics in Iowa, so from
    seven in the evening Central the machine has already turned the page. A
    workbook built then was stamped tomorrow, and B3 is what the SOL sheet's
    twenty year cut and both expungement waits are measured against, so every
    one of them was measured a day early. Nobody would see it: the sheet would
    just say a client was eligible.

    Found on 2026-08-01 by running the real thing at eleven at night and
    reading the date off the file that came back.
    """

    def _at(self, monkeypatch, when):
        """Run the build with the clock held at one instant."""
        real = crs.datetime

        class Held(real):
            @classmethod
            def now(cls, tz=None):
                return when.astimezone(tz) if tz else when.replace(tzinfo=None)

        monkeypatch.setattr(crs, 'datetime', Held)
        # The dyno's own idea of the day, which is what the old code read.
        monkeypatch.setattr(tasks.datetime, 'date', type(
            'UtcDate', (datetime.date,),
            {'today': classmethod(lambda cls: when.date())}))
        sheet, path = _build()
        try:
            value = sheet['B3'].value
            return value.date() if isinstance(value, datetime.datetime) else value
        finally:
            os.remove(path)

    def test_a_late_evening_clinic_is_not_dated_tomorrow(self, monkeypatch):
        # 03:30 UTC on the 2nd is 22:30 on the 1st in Des Moines.
        night = datetime.datetime(2026, 8, 2, 3, 30,
                                  tzinfo=datetime.timezone.utc)
        assert self._at(monkeypatch, night) == datetime.date(2026, 8, 1)

    def test_and_a_daytime_clinic_is_still_dated_today(self, monkeypatch):
        """The fix must not move the ordinary case, which is every clinic."""
        morning = datetime.datetime(2026, 8, 1, 15, 0,
                                    tzinfo=datetime.timezone.utc)
        assert self._at(monkeypatch, morning) == datetime.date(2026, 8, 1)


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
