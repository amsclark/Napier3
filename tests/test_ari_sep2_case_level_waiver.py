"""Iowa Legal Aid's 2 September 2026 answer: the waiver wins from the status too.

The 1 September change made the two waiver wordings outrank everything else in
JUVENILE_DISPOSITIONS, so a juvenile case whose counts show a waiver reads JWV
whatever the other counts say. Asked to confirm that on 2 September, Arianna
Eddy answered "we want JWV to trump JUV in that situation".

The counts are not the only place the waiver shows. ICOS prints a status for
the case as a whole, and TRANSFERRED there is the same waiver up to adult
court. Napier read that status only where no count had been adjudicated, so a
JV case with one adjudicated count and TRANSFERRED as its status still came out
JUV -- the reading their rule says is wrong. Now the juvenile waiver is taken
from either place.

Only JWV is taken this way, and only on a juvenile docket: TRANSFERRED on an
adult case is a change of venue, and every other case-level wording on an
adjudicated case stays unread rather than talking over the counts or mailing
out a wording Napier did not need to code.

Every case number here is synthetic. The repository is public.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs
from test_civil_case_types import _case, _row, FULL

from openpyxl import load_workbook

JUVENILE = '00000  JVJV000000'
FELONY = '00000  FECR000000'

WAIVERS = ['TRANSFERRED', 'WAIVED TO ADULT COURT']

UNADJUDICATED = [('714.2(3)', 'SYNTHETIC THEFT', None, '')]


def count(wording):
    return [('714.2(3)', 'SYNTHETIC THEFT', wording, '02/02/1901')]


def _cells(case_id, counts, status=''):
    """Column D as well as G, which _row does not carry."""
    sheet = load_workbook(FULL)['CASE DATA']
    crs.process_case(_case(case_id, counts, status), sheet, crs.FIRST_CASE_ROW)
    row = str(crs.FIRST_CASE_ROW)
    return {'D': sheet['D' + row].value, 'G': sheet['G' + row].value}


# -- what Iowa Legal Aid asked for -------------------------------------------

@pytest.mark.parametrize('waiver', WAIVERS)
@pytest.mark.parametrize('other', ['ADJUDICATED', 'JUVENILE ADMISSION',
                                   'CONSENT DECREE', 'DISMISSED'])
def test_a_case_level_waiver_beats_the_code_the_counts_earned(waiver, other):
    """The shape that read JUV until this change: the waiver is on the case,
    not on any count, and a count is coded."""
    assert _row(JUVENILE, count(other), waiver)['G'] == 'JWV'


@pytest.mark.parametrize('waiver', WAIVERS)
def test_the_waiver_still_wins_when_it_is_on_a_count(waiver):
    """The 1 September half of the rule, unchanged."""
    assert _row(JUVENILE, count('ADJUDICATED') + count(waiver))['G'] == 'JWV'


@pytest.mark.parametrize('waiver', WAIVERS)
def test_the_waiver_still_wins_with_nothing_adjudicated(waiver):
    """The path that already read the status, unchanged."""
    assert _row(JUVENILE, UNADJUDICATED, waiver)['G'] == 'JWV'


# -- and what it must not do -------------------------------------------------

@pytest.mark.parametrize('waiver', WAIVERS)
def test_the_waiver_does_not_date_the_case(waiver):
    """A waiver up is not an adjudication. Column D is what the EXPUNGEMENT
    sheet reads to decide a charge is not still pending, so the date stays the
    adjudicated count's rather than being moved or blanked."""
    assert _cells(JUVENILE, count('ADJUDICATED'), waiver)['D'] == '02/02/1901'


def test_transferred_on_an_adult_case_is_still_a_change_of_venue():
    """A change of venue on a felony docket, and no juvenile code can reach an
    adult case however the clerk typed the status."""
    row = _cells(FELONY, count('GUILTY'), 'TRANSFERRED')
    assert row['G'] == 'GTR'


def test_a_waiver_worded_status_cannot_reach_an_adult_case():
    assert _row(FELONY, count('GUILTY'), 'WAIVED TO ADULT COURT')['G'] == 'GTR'


@pytest.mark.parametrize('status', ['CLOSED', 'VIOLATIONS HANDLED BY CLERK',
                                    'OTHER JUDGMENT', 'DISMISSED'])
def test_no_other_status_talks_over_the_counts(status):
    """Only JWV is read off the status of an adjudicated case. The counts are
    the better evidence of how the case was decided, and a status Napier
    cannot translate must not start alerting on cases it coded correctly."""
    row = _row(JUVENILE, count('ADJUDICATED'), status)
    assert row['G'] == 'JUV'
    assert row['reported'] == []
