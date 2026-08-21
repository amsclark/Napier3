"""Four docket types Iowa Legal Aid read as civil on 21 August 2026.

AMCR is the appeal misdemeanour docket, and it is where the county files the
parole holds, contempts and fugitive warrants that have come back to Napier
four separate times under four different codes. Iowa Legal Aid report a recent
decision reading the whole type as civil. DRCV is the protective order docket,
civil on its face, and the one their 20 August run left uncoded because OTHER
JUDGMENT on a civil case had no settled answer.

Those two shipped first. On seeing them land, Iowa Legal Aid asked for AMCV
and DRCR the same afternoon, which is every AM and DR type ICOS has shown us.
Their ground is the docket rather than the CR/CV suffix: the decision reads
the appeal misdemeanour type as civil however the clerk styled it, and the
criminal-styled DR cases are contempts off a protective order rather than
convictions of their own.

So the answer stopped being about the wording. On these four types the docket
decides, and column G reads CIV whatever the clerk typed on the counts. That
is a blunter rule than anything else in crs.py, which is why the guard rails
below are the larger half of this file: four-character types, no prefix
matching, and every other docket untouched.

What it costs is written down in CIVIL_CASE_TYPES and tested here: a guilty
count on one of these types sorts as no conviction on BANKRUPTCY and
EXEMPTIONS, and a dismissal on one stops answering YES in the expungement
sheet's DISM ACQ? column. Iowa Legal Aid has settled that trade before, on the
ground that a civil case is not eligible for dismissed-or-acquitted
expungement in the first place.

Every case number here is synthetic. The repository is public.
"""

import os
import sys
from decimal import Decimal

import pytest
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs
from test_multi_count import charges_page

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, 'CRS 3.5.5.xlsx')

APPEAL_MISDEMEANOUR = '00000  AMCR000000'
APPEAL_MISDEMEANOUR_CV = '00000  AMCV000000'
PROTECTIVE_ORDER = '00000  DRCV000000'
PROTECTIVE_ORDER_CR = '00000  DRCR000000'
FELONY = '00000  FECR000000'

# Every type the rule reads as civil, which is what the parametrised tests
# below run over. Named once so a fifth type added to CIVIL_CASE_TYPES without
# a decision behind it fails the whitelist test rather than quietly widening
# every case here.
CIVIL_DOCKETS = [APPEAL_MISDEMEANOUR, APPEAL_MISDEMEANOUR_CV,
                 PROTECTIVE_ORDER, PROTECTIVE_ORDER_CR]

GUILTY = [('714.2(3)', 'SYNTHETIC THEFT', 'GUILTY', '02/02/1901')]
DISMISSED = [('714.2(3)', 'SYNTHETIC THEFT', 'DISMISSED', '02/02/1901')]
UNREADABLE = [('714.2(3)', 'SYNTHETIC THEFT',
               'SYNTHETIC WORDING NOBODY HAS SEEN', '02/02/1901')]
UNADJUDICATED = [('714.2(3)', 'SYNTHETIC THEFT', None, '')]


def _case(case_id, counts, status='', costs='197.43'):
    case = {'id': case_id, 'county': 'SYNTHETIC',
            'financials': [], 'sentences': [],
            'summary_created_date': '01/01/1900',
            'summary_disposition_date': '02/02/1901',
            'summary_dispo_status': status}
    case_parser.parse_case_charges(charges_page(counts), case)
    case['summary_categories'] = [
        {'label': label,
         'original': Decimal(costs) if label == 'COSTS' else Decimal('0'),
         'paid': Decimal('0'),
         'due': Decimal(costs) if label == 'COSTS' else Decimal('0')}
        for label in ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER')]
    case['total_due'] = '$' + costs
    return case


def _row(case_id, counts, status=''):
    """Column G, column V and what the run was told, off one built row."""
    sheet = load_workbook(FULL)['CASE DATA']
    unknown = crs.process_case(_case(case_id, counts, status), sheet,
                               crs.FIRST_CASE_ROW)
    row = str(crs.FIRST_CASE_ROW)
    return {'G': sheet['G' + row].value,
            'V': sheet['V' + row].value or '',
            'reported': unknown}


# -- what Iowa Legal Aid asked for -------------------------------------------

@pytest.mark.parametrize('case_id', CIVIL_DOCKETS)
@pytest.mark.parametrize('counts', [GUILTY, DISMISSED, UNREADABLE])
def test_the_docket_type_decides_the_code(case_id, counts):
    """Whatever the clerk typed on the counts, including nothing Napier reads."""
    assert _row(case_id, counts)['G'] == 'CIV'


@pytest.mark.parametrize('case_id', CIVIL_DOCKETS)
def test_it_also_decides_a_case_with_no_adjudication_at_all(case_id):
    """The shape the 20 August run raised: nothing adjudicated on any count, so
    column G used to read the case-level status or stay empty. An empty column
    G is what BANKRUPTCY, EXEMPTIONS and SOL print as "open charge"."""
    assert _row(case_id, UNADJUDICATED, 'OTHER JUDGMENT')['G'] == 'CIV'


def test_transferred_on_an_appeal_misdemeanour_is_civil_now():
    """The case from the 21 August email, which read TNSF the day before."""
    assert _row(APPEAL_MISDEMEANOUR, UNADJUDICATED, 'TRANSFERRED')['G'] == 'CIV'


# -- and what it must not reach ----------------------------------------------

@pytest.mark.parametrize('case_id', [
    FELONY,
    '00000  AGCR000000',
    '00000  SMCR000000',
    '00000  JVJV000000',
    # Synthetic neighbours on the same two letters. All four real AM and DR
    # types are civil now, so these stand in for whatever Iowa adds next: a
    # prefix rule would take them the day they appeared, and this rule does
    # not. Adding a fifth type is meant to cost an email, the way these did.
    '00000  AMXX000000',
    '00000  DRXX000000',
])
def test_every_other_docket_reads_its_counts_as_before(case_id):
    assert _row(case_id, GUILTY)['G'] == 'GTR'


def test_an_unreadable_wording_still_alerts_off_these_types():
    """The vocabulary discovery this rule switches off on the civil dockets
    is not switched off anywhere else, so a wording Napier cannot read is
    still reported the first time it lands on a docket that has to code it."""
    assert _row(FELONY, UNREADABLE)['reported'] == [
        ('SYNTHETIC WORDING NOBODY HAS SEEN', True)]


# -- the row does not say things that are not so -----------------------------

@pytest.mark.parametrize('case_id', CIVIL_DOCKETS)
def test_the_row_does_not_claim_to_be_coded_oth(case_id):
    """The two unknown-disposition notes both describe a row coded on a guess.
    Neither describes this row: the docket answered, and it answers the same
    way whatever the clerk typed."""
    note = _row(case_id, UNREADABLE)['V']
    assert 'OTH' not in note, note
    assert 'does not recognise' not in note, note


@pytest.mark.parametrize('case_id', CIVIL_DOCKETS)
def test_the_run_is_not_told_to_go_and_code_it(case_id):
    """Same sentence, in the half Alex reads. An alert saying these rows are
    coded OTH would be pointing at a column G that reads CIV."""
    assert _row(case_id, UNREADABLE)['reported'] == []


@pytest.mark.parametrize('case_id', CIVIL_DOCKETS)
def test_the_row_does_not_claim_to_be_an_open_charge(case_id):
    """The other half of the same defect, on the unadjudicated shape."""
    assert 'open charge' not in _row(case_id, UNADJUDICATED, 'CLOSED')['V']


# -- the reading itself ------------------------------------------------------

def test_the_whitelist_is_exactly_these_four_types():
    """The list Iowa Legal Aid decided, spelled out rather than derived. A
    fifth type belongs here only after somebody has answered for it."""
    assert crs.CIVIL_CASE_TYPES == ('AMCR', 'AMCV', 'DRCR', 'DRCV')


@pytest.mark.parametrize('case_id,expected', [
    (APPEAL_MISDEMEANOUR, True),
    (APPEAL_MISDEMEANOUR_CV, True),
    (PROTECTIVE_ORDER, True),
    (PROTECTIVE_ORDER_CR, True),
    (FELONY, False),
    ('00000  AMXX000000', False),
    ('00000  DRXX000000', False),
    ('', False),
    (None, False),
])
def test_is_civil_case_type_reads_the_four_characters(case_id, expected):
    assert crs.is_civil_case_type(case_id) is expected
