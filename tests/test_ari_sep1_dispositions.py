"""Iowa Legal Aid's 1 September 2026 disposition answers, juvenile half.

Their review of a real workbook settled two juvenile questions with one rule,
stated for the third time since 26 August: a juvenile case reads JUV unless it
was waived to adult court. So a count dismissed or withdrawn on a JVJV case
reads JUV, not DISM or WTHD, and the DEFERRED MISTRIAL count that alerted on
27 August reads JUV too. The adult readings of all five wordings are
untouched.

The same rule's other half is the waiver: TRANSFERRED and WAIVED TO ADULT
COURT on a juvenile docket now outrank everything else in either map, so a JV
case showing a waiver alongside anything else reads JWV. Before this the
juvenile waiver ranked 0 and an adjudicated count would win the case code,
which read a waived-up case as JUV.

The CDDM answer from the same email lives in test_civil_case_types.py.
Every case number here is synthetic. The repository is public.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs
from test_civil_case_types import _row

JUVENILE = '00000  JVJV000000'
FELONY = '00000  FECR000000'

# The five wordings the 1 September answer moved, with the code each still
# earns on an adult docket.
RULED = [
    ('DISMISSED', 'DISM'),
    ('DISMISSED BY COURT', 'DISM'),
    ('DISMISSED BY OTHER', 'DISM'),
    ('WITHDRAWN', 'WTHD'),
    ('DEFERRED MISTRIAL', 'OTH'),
]


def count(wording):
    return [('714.2(3)', 'SYNTHETIC THEFT', wording, '02/02/1901')]


# -- what Iowa Legal Aid asked for -------------------------------------------

@pytest.mark.parametrize('wording,adult_code', RULED)
def test_the_wording_reads_juv_on_a_juvenile_docket(wording, adult_code):
    row = _row(JUVENILE, count(wording))
    assert row['G'] == 'JUV'
    # Known wording, so nothing for the run to go and code.
    assert row['reported'] == []


@pytest.mark.parametrize('wording,adult_code', RULED)
def test_the_adult_docket_still_reads_its_own_code(wording, adult_code):
    assert _row(FELONY, count(wording))['G'] == adult_code


@pytest.mark.parametrize('wording,adult_code', RULED)
def test_the_column_e_suffix_agrees_with_column_g(wording, adult_code):
    """case_parser writes the [CODE] suffix on column E off its own map, so
    the juvenile reading has to land in both modules or the row contradicts
    itself -- the GUILTY - OTHER failure the twin-map tests guard."""
    assert case_parser.disposition_code(wording, JUVENILE) == 'JUV'
    assert case_parser.disposition_code(wording, FELONY) == adult_code
    assert case_parser.disposition_code(wording) == adult_code


# -- unless it was waived up -------------------------------------------------

@pytest.mark.parametrize('waiver', ['TRANSFERRED', 'WAIVED TO ADULT COURT'])
@pytest.mark.parametrize('other', ['DISMISSED', 'CONSENT DECREE'])
def test_a_waiver_alongside_anything_reads_jwv(waiver, other):
    """The 'unless' half of the rule. A juvenile case that shows a waiver was
    waived up, whatever its other counts say, so JWV wins the case code over
    the JUV the other count would earn."""
    assert _row(JUVENILE, count(other) + count(waiver))['G'] == 'JWV'


def test_transferred_on_an_adult_docket_is_still_a_change_of_venue():
    assert _row(FELONY, count('TRANSFERRED'))['G'] == 'TNSF'


# -- reading JUV does not mean dating the case -------------------------------

from test_adult_adjudication import _charge


def test_a_dismissed_count_does_not_date_the_case():
    """The regression the first cut of this change made. Column D pairs the
    winning code with the earliest count that produced it, and a dismissed
    count now produces JUV on a juvenile case, so a dismissal disposed before
    the adjudication was dating the whole row. JUVENILE_UNDATED keeps the
    dismissals out of the date pairing: they earn the code, not the date."""
    charge = crs.get_dominant_charge(
        _charge(['DISMISSED BY COURT', 'ADJUDICATED'],
                ['02/02/1901', '03/03/1903']), JUVENILE)
    assert charge['disposition'] == 'JUV'
    assert charge['dispositionDate'] == '03/03/1903'


def test_a_case_with_only_dismissals_keeps_its_own_date():
    """Nothing else to date the row, so the parser's date stands rather than
    column D going empty and reading as an open charge on three sheets."""
    charge = crs.get_dominant_charge(
        _charge(['DISMISSED'], ['02/02/1901']), JUVENILE)
    assert charge['disposition'] == 'JUV'
    assert charge['dispositionDate'] == '02/02/1901'


# -- the rule does not leak off the juvenile docket --------------------------

def test_a_wording_only_the_juvenile_table_knows_still_alerts_elsewhere():
    """DISCHARGE has no adult entry, and the juvenile answer must not give it
    one: an adult case carrying it stays visibly uncoded."""
    assert crs.case_level_code('DISCHARGE', FELONY) is None
    assert crs.case_level_code('DISCHARGE', JUVENILE) == 'JUV'
