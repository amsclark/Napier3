"""A domestic case with a support obligation loses its whole itemization.

Reported by Iowa Legal Aid on 20 August: a sheriff's fee still coming out in
the wrong column after the sheriff fix shipped. The fix was fine. It was never
reached.

An ICOS financials page normally carries two forms, and the itemization is the
first of them. A case with a support or alimony obligation gets a "Pay Rec"
button inside the summary table at the top, and that button is a form of its
own, so the page carries three and the first holds no rows at all. The parser
took soup.find('form'), read no itemization, and handed the reconciliation an
empty list. reconcile_financials bails on an empty itemization by design, so
every dollar fell through to the five-bucket summary, and COSTS -- which is
where a sheriff's fee sits in the rollup -- matches no fee wording and lands
in MISCELLANEOUS.

The fixture is a real page with the parties and the case number scrubbed out.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import case_parser
import crs

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


@pytest.fixture
def support_case():
    path = os.path.join(FIXTURES, 'financials_support_case_sample.html')
    with open(path, 'rb') as handle:
        html = handle.read()
    case = {'id': '00000  DRCV000000'}
    case_parser.parse_case_financials(html, case)
    return case


def test_the_page_really_does_carry_three_forms(support_case):
    """Guard the premise, so a future fixture edit cannot quietly defuse this.

    Including that the first of them is the empty one, which is the whole
    reason position is not a safe way to find the itemization.
    """
    from bs4 import BeautifulSoup

    path = os.path.join(FIXTURES, 'financials_support_case_sample.html')
    with open(path, 'rb') as handle:
        html = handle.read().decode('utf-8', 'ignore')
    forms = BeautifulSoup(html, 'html.parser').find_all('form')
    assert len(forms) == 3
    assert forms[0].find_all('tr') == []


def test_itemization_is_read_past_the_pay_rec_form(support_case):
    details = [row['detail'] for row in support_case['financials']]
    assert details == ['FILING AND DOCKETING PETITION EXCL DISSO',
                       'CHILD SUPPORT',
                       'SHERIFFS FEES - LOCAL']


def test_the_sheriff_fee_reaches_the_sheriff_column(support_case):
    columns, _note = crs.reconcile_financials(support_case)
    assert columns is not None, "reconciliation bailed, so the fee is in MISC"
    assert columns.get('M') == Decimal('62.66')


def test_the_filing_fee_is_still_costs(support_case):
    columns, _note = crs.reconcile_financials(support_case)
    assert columns.get('O') == Decimal('195.00')


def test_the_summary_still_parses(support_case):
    labels = [c['label'] for c in support_case['summary_categories']]
    assert labels == ['COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER']
    assert support_case['total_due'] == '$257.66'
