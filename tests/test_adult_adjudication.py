"""When "Adjudicated" is not the juvenile court speaking.

Iowa Legal Aid reported this on 3 August 2026, having lived with it long enough
to have a manual workaround:

    Column G is pulling the violation of probation disposition code as "JUV"
    when it should select the conviction disposition as GPL. The old one does
    this too and we have to manually switch it, but if it can only say JUV when
    the case number is "JVJV" that would be cool. The issue is that the clerks
    are using "Adjudicated" for the probation violations when they should only
    use that for juvenile cases.

ADJUDICATED and GUILTY - NEGOTIATED/VOLUN PLEA were ranked equal, so on a felony
carrying both, the probation violation spoke for the case.

Their second report is the same bug seen from the next column along:

    Column D is putting a violation of probation/contempt disposition date
    instead of the actual disposition date of the cases.

Column D is dated by whichever count wins column G, so demoting the adjudication
moves both. Two of the three captured cases carrying an adjudication are
felonies of exactly this shape.

What it costs, beyond the manual switch: BANKRUPTCY and EXEMPTIONS both list JUV
among the codes meaning no conviction, so a felony reading JUV came out of two
sheets with its debt marked dischargeable, and the licence and expungement
sheets test for GTR, GPL and DEF and saw none of them.

Every case number here is synthetic. The repo is public.
"""

import os
import sys
from decimal import Decimal

import pytest
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, 'CRS 3.5.5.xlsx')

FELONY = '00000  FECR000000'
JUVENILE = '00000  JVJV000000'


def _charge(dispositions, dates=None):
    """One ICOS charge block carrying several counts, as the parser leaves it.

    dispositionDate starts as the first count's date, which is what the parser
    hands over and what get_dominant_charge then corrects to the winning count's.
    """
    dates = list(dates or [])
    return [{'charge': '124.401', 'description': 'SYNTHETIC OFFENSE',
             'disposition': list(dispositions),
             'disposition_dates': dates,
             'dispositionDate': dates[0] if dates else '',
             'offenseDate': '01/01/1900'}]


# -- the case number, read the one way it is read here -----------------------

def test_the_docket_type_comes_out_of_the_case_number():
    assert crs.case_type(FELONY) == 'FECR'
    assert crs.case_type(JUVENILE) == 'JVJV'


@pytest.mark.parametrize('case_id', [None, '', '00000', '00000  FE'])
def test_a_case_number_too_short_to_hold_a_type_yields_nothing(case_id):
    """Rather than an IndexError in the middle of a run."""
    assert crs.case_type(case_id) == ''


@pytest.mark.parametrize('case_id', [JUVENILE, '00000  JVCV000000',
                                     '00000  JVDV000000'])
def test_the_juvenile_docket_is_recognised(case_id):
    """JVJV is what Iowa Legal Aid named. The rest of the JV family is accepted
    too, because failing to recognise a real juvenile case is the one error this
    demotion could introduce."""
    assert crs.is_juvenile_case(case_id) is True


@pytest.mark.parametrize('case_id', [FELONY, '00000  SMCR000000',
                                     '00000  SCSC000000', None])
def test_nothing_else_is(case_id):
    assert crs.is_juvenile_case(case_id) is False


# -- the reported defect -----------------------------------------------------

@pytest.mark.parametrize('counts', [
    ['ADJUDICATED', 'DISMISSED BY COURT', 'GUILTY - NEGOTIATED/VOLUN PLEA'],
    ['DISMISSED BY COURT', 'GUILTY - NEGOTIATED/VOLUN PLEA', 'ADJUDICATED'],
    ['GUILTY - NEGOTIATED/VOLUN PLEA', 'ADJUDICATED', 'DISMISSED BY COURT'],
])
def test_a_negotiated_plea_beats_a_probation_violation_adjudication(counts):
    """The reported case, and the shape of two of the three captured ones.

    Every order of the same three counts, because equal ranks were broken by
    whichever count ICOS happened to print first. That is why the defect looked
    intermittent: the same three dispositions in a different order gave a
    different answer, and only the pages that listed the adjudication first came
    out as JUV.
    """
    charge = crs.get_dominant_charge(_charge(counts), FELONY)
    assert charge['disposition'] == 'GPL'


def test_a_guilty_verdict_beats_it_too():
    charge = crs.get_dominant_charge(
        _charge(['ADJUDICATED', 'GUILTY']), FELONY)
    assert charge['disposition'] == 'GTR'


def test_a_deferred_judgment_still_outranks_everything():
    charge = crs.get_dominant_charge(
        _charge(['ADJUDICATED', 'DEFERRED']), FELONY)
    assert charge['disposition'] == 'DEF'


def test_the_juvenile_court_keeps_its_own_word():
    """The rule has to leave the case the word was meant for alone."""
    charge = crs.get_dominant_charge(
        _charge(['ADJUDICATED', 'DISMISSED BY COURT']), JUVENILE)
    assert charge['disposition'] == 'JUV'


def test_an_adjudication_still_beats_a_dismissal_on_an_adult_case():
    """Demoted below a conviction, not struck out. A case whose other counts
    were all dismissed has still been adjudicated on one of them."""
    charge = crs.get_dominant_charge(
        _charge(['ADJUDICATED', 'DISMISSED BY COURT', 'ACQUITTED']), FELONY)
    assert charge['disposition'] == 'JUV'


def test_an_adjudication_still_beats_an_unreadable_code():
    charge = crs.get_dominant_charge(
        _charge(['ADJUDICATED', 'SYNTHETIC WORDING NOBODY HAS SEEN']), FELONY)
    assert charge['disposition'] == 'JUV'


def test_no_case_number_reads_as_not_juvenile():
    """The way round that cannot invent a JUV on an adult case. process_case is
    the only caller that matters and it always has the number."""
    charge = crs.get_dominant_charge(
        _charge(['ADJUDICATED', 'GUILTY - NEGOTIATED/VOLUN PLEA']))
    assert charge['disposition'] == 'GPL'


# -- the date follows the code -----------------------------------------------

def test_column_d_takes_the_conviction_date_not_the_violation_date():
    """Their second report. The plea was in 1900 and the probation violation
    was found in 1903, and the row was dated 1903.

    Adjudication first, which is the order that produced the defect."""
    charge = crs.get_dominant_charge(
        _charge(['ADJUDICATED', 'DISMISSED BY COURT',
                 'GUILTY - NEGOTIATED/VOLUN PLEA'],
                ['03/03/1903', '02/02/1901', '01/01/1900']), FELONY)
    assert charge['disposition'] == 'GPL'
    assert charge['dispositionDate'] == '01/01/1900'


def test_a_juvenile_case_is_still_dated_by_its_adjudication():
    charge = crs.get_dominant_charge(
        _charge(['DISMISSED BY COURT', 'ADJUDICATED'],
                ['02/02/1901', '03/03/1903']), JUVENILE)
    assert charge['dispositionDate'] == '03/03/1903'


# -- what the row says -------------------------------------------------------

def _row(case_id, dispositions, dates=None):
    sheet = load_workbook(FULL)['CASE DATA']
    case = {'id': case_id, 'county': 'SYNTHETIC',
            'charges': _charge(dispositions, dates),
            'financials': [], 'sentences': [], 'summary_categories': [],
            'total_due': '$0.00'}
    crs.process_case(case, sheet, crs.FIRST_CASE_ROW)
    row = str(crs.FIRST_CASE_ROW)
    return {column: sheet[column + row].value for column in ('D', 'G', 'V')}


def test_the_felony_row_reads_as_a_conviction():
    cells = _row(FELONY, ['ADJUDICATED', 'GUILTY - NEGOTIATED/VOLUN PLEA'],
                 ['03/03/1903', '01/01/1900'])
    assert cells['G'] == 'GPL'
    assert cells['D'] == '01/01/1900'


def test_the_felony_row_carries_no_caveat_about_it():
    """The common case is the clerk's habit, not an anomaly, and a note on every
    probation violation is noise on top of the money caveats column V exists
    for."""
    cells = _row(FELONY, ['ADJUDICATED', 'GUILTY - NEGOTIATED/VOLUN PLEA'])
    assert not cells['V'] or 'Adjudicated' not in cells['V']


def test_an_adult_case_that_still_reads_juv_says_so():
    """Nothing in 300 captured cases takes this path. If one turns up, the row
    has to carry the doubt: JUV clears BANKRUPTCY and EXEMPTIONS."""
    cells = _row(FELONY, ['ADJUDICATED', 'DISMISSED BY COURT'])
    assert cells['G'] == 'JUV'
    assert 'not a juvenile case number' in cells['V']


def test_the_juvenile_row_is_left_alone():
    cells = _row(JUVENILE, ['ADJUDICATED'])
    assert cells['G'] == 'JUV'
    assert not cells['V'] or 'not a juvenile case number' not in cells['V']


# -- the note Iowa Legal Aid misread -----------------------------------------

def test_the_spread_note_does_not_claim_column_g_holds_a_date():
    """Their words: "The note says that column G will hold the disposition date,
    but that one should just have the dispo code". It never held a date, and
    two drafts written here were misread in a row. The wording is now the
    sentence Iowa Legal Aid supplied on 18 August 2026, which does not
    mention column G at all: column D counts the conviction date, and an SOL
    reader goes back to ICOS."""
    note = crs.DISPOSITION_SPREAD_NOTE % (2, '01/01/1900, 03/03/1903',
                                          '01/01/1900')
    assert 'Column D counts the conviction date' in note
    assert 'column G' not in note
    assert 'the date of the disposition in column G' not in note
