"""Column H, "Vehicular?", and the sheet that has been reading it empty.

LICENSE-REGIS asks one question per case: does unpaid debt on this case cost the
client a driver's licence, or only a vehicle registration. It answers it from
CASE DATA column H, and Napier has never written column H, so the answer has
been "Registration only" on every case of every workbook the app has produced.
That is not a formatting problem. A client is told their licence is safe when
it is not.

The first half of this file pins what Napier now writes there. The second half
pins why "YES" is the string, by reading the formula back out of the template
rather than trusting a comment: if CRS 3.6 renames the sentinel or moves the
column, the sheet goes quietly wrong again and these fail instead.
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


# -- what Napier writes -----------------------------------------------------

VEHICULAR = [
    ('321J.2', 'OWI, the reason most of these clients lose a licence'),
    ('321.218', 'driving while barred'),
    ('321.174', 'no valid licence'),
    ('321.560', 'habitual offender'),
    ('321A.32', 'financial responsibility'),
    ('321.20A', 'a subsection, so the chapter test cannot key on length'),
    ('707.6A', 'homicide by vehicle, chapter 707 but revokes a licence'),
    ('707.6A(1)', 'the same with a subsection'),
]


@pytest.mark.parametrize('statute,why', VEHICULAR)
def test_a_vehicular_statute_says_yes(statute, why):
    assert crs.is_vehicular(statute) == "YES", why


NOT_VEHICULAR = [
    ('124.401', 'controlled substances'),
    ('714.1', 'theft'),
    ('708.2', 'assault'),
    ('707.6', 'not 707.6A. The vehicular section is the one with the A'),
    ('32.1', 'chapter 32, which is not 321 and must not match on a prefix'),
    ('3210.1', 'no such chapter, but a prefix test would call it vehicular'),
]


@pytest.mark.parametrize('statute,why', NOT_VEHICULAR)
def test_a_statute_that_is_not_vehicular_says_no(statute, why):
    assert crs.is_vehicular(statute) == "NO", why


NOTHING_TO_JUDGE = [
    ('', 'every count was dismissed, so column F is empty'),
    (None, 'no charge at all'),
    ('n/a', 'a civil case, which is what the civil branch writes into F'),
    ('  ', 'whitespace'),
]


@pytest.mark.parametrize('statutes,why', NOTHING_TO_JUDGE)
def test_it_says_nothing_when_there_is_nothing_to_say(statutes, why):
    """Blank, not "NO".

    A blank and a "NO" read the same to the sheet, so this costs nothing there
    and it stops the workbook asserting to a reader that Napier checked and
    found no vehicular charge when it had nothing to check.
    """
    assert crs.is_vehicular(statutes) is None, why


def test_one_vehicular_count_carries_the_case():
    """Column F is semicolon-joined and the licence follows any count in it.

    Plead to possession and OWI together and column G says GPL once for the
    whole case, but the licence consequence attaches to the OWI whichever count
    the ranking picked to speak for it.
    """
    assert crs.is_vehicular('124.401;321J.2') == "YES"
    assert crs.is_vehicular('321J.2;124.401') == "YES"


def test_several_counts_none_of_them_vehicular():
    assert crs.is_vehicular('124.401;714.1;708.2') == "NO"


def test_spacing_around_the_semicolon_does_not_matter():
    assert crs.is_vehicular('124.401; 321J.2') == "YES"


# -- and that it reaches the cell -------------------------------------------

# The tests above would all have passed against a version of Napier that never
# wrote column H, because they call is_vehicular directly. These are the ones
# that would not: they write a case into a worksheet the way a real run does.

def _case(statutes, disposition='GUILTY BY PLEA'):
    return {
        'id': '00000  FECR000000',
        'county': 'SYNTHETIC',
        'charges': [{
            'charge': statutes,
            'description': 'SYNTHETIC OFFENSE',
            'disposition': [disposition],
            'offenseDate': '01/01/1900',
            'dispositionDate': '02/02/1901',
        }],
        # No money on the case. The financial columns have their own tests.
        'financials': [],
        'summary_categories': [],
    }


def _written(case):
    sheet = load_workbook(FULL)['CASE DATA']
    crs.process_case(case, sheet, crs.FIRST_CASE_ROW)
    row = str(crs.FIRST_CASE_ROW)
    return sheet['F' + row].value, sheet['H' + row].value


def test_a_vehicular_case_reaches_column_h():
    assert _written(_case('321J.2')) == ('321J.2', 'YES')


def test_a_non_vehicular_case_reaches_column_h():
    assert _written(_case('124.401')) == ('124.401', 'NO')


def test_a_case_with_no_adjudicated_statute_leaves_column_h_alone():
    """Every count dismissed. Column F is empty and so is H."""
    assert _written(_case('', disposition='DISMISSED')) == ('', None)


def test_a_civil_case_leaves_column_h_alone():
    """The civil branch writes "n/a" into F, which is not a statute."""
    civil = {
        'id': '00000  SCSC000000',
        'county': 'SYNTHETIC',
        'charges': [],
        'summary_created_date': '01/01/1900',
        'summary_disposition_date': '02/02/1901',
        'summary_dispo_status': 'SYNTHETIC STATUS',
        'financials': [],
        'summary_categories': [],
    }
    assert _written(civil) == ('n/a', None)


# -- what the sheet does with it --------------------------------------------

def _license_regis_formula(path):
    """The first LICENSE-REGIS formula that reads CASE DATA column H."""
    sheet = load_workbook(path)['LICENSE-REGIS']
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and "'CASE DATA'!H" in cell.value:
                return cell.value.replace('\n', '')
    return None


def test_the_full_template_asks_column_h_about_the_licence():
    formula = _license_regis_formula(FULL)
    assert formula is not None, "LICENSE-REGIS stopped reading column H"
    assert 'License & registration' in formula
    assert 'Registration only' in formula


def test_yes_is_the_string_the_sheet_is_testing_for():
    """The reason is_vehicular returns "YES" and not True or "Y".

    The sheet compares column H against a literal. Anything else Napier could
    write there is indistinguishable from an empty cell, which is exactly the
    state this whole change is fixing.
    """
    formula = _license_regis_formula(FULL)
    match = re.search(r"'CASE DATA'!H\d+\s*=\s*\"([^\"]*)\"", formula)
    assert match, formula
    assert match.group(1) == "YES"


def test_the_lite_template_asks_the_same_question():
    """Lite drops three sheets but keeps this one, and staff use it far more."""
    formula = _license_regis_formula(LITE)
    assert formula is not None
    match = re.search(r"'CASE DATA'!H\d+\s*=\s*\"([^\"]*)\"", formula)
    assert match and match.group(1) == "YES", formula


def test_the_sheet_only_asks_once_the_case_is_a_conviction_with_debt():
    """Context for the blank-when-unknown choice above.

    Column H is only reached when the case has debt and a conviction code, so a
    civil case or an all-dismissed case never gets asked. Writing "NO" into
    those rows would have been noise that changed no answer.
    """
    formula = _license_regis_formula(FULL)
    assert 'GTR' in formula and 'GPL' in formula and 'DEF' in formula
