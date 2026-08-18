"""The two court-side figures the ability-to-pay calculator asks for.

The calculator at abilitytopay.org works out what someone can actually pay on
their court debt. Most of what it asks, income and rent and groceries, only the
client can answer. Two of its inputs are court record: what is owed, and what
has been going toward it each month. Those are the ones a person sitting in a
clinic cannot answer from memory, and they are the ones Napier already knows.

Both are in the workbook. What is pinned here is that the screen says the same
thing the file says, that a client with no itemized payments is never handed
over as having paid zero, and that neither figure gets anywhere near an email.

Every case number is the synthetic 00000 FECR000000 and no real person
appears anywhere in here, because this repository is public.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import actions
import app as app_module
import crs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, 'CRS 3.5.5.xlsx')

CLINIC = date(2026, 7, 31)
FRESH = '01/01/2020'


@pytest.fixture
def done_page():
    """The single-client finish page, rendered with figures we choose."""
    def render(balance="$500.00", monthly="$40.00"):
        app_module.app.secret_key = 'test'
        with app_module.app.test_request_context():
            return app_module.render_template(
                'done.html',
                job={'id': 'jobjobjob'},
                atp={'balance': balance, 'monthly': monthly,
                     'months': crs.RECENT_MONTHS},
                def_name='SYNTHETIC CLIENT', is_lite=False, written=2,
                requested=2, failed=[], can_retry=False, error=None,
                missing=0, limits=[], filename='SYNTHETIC_CRS.xlsx')
    return render


@pytest.fixture
def batch_page():
    """The clinic-list finish page, one row per client."""
    def render(figures):
        app_module.app.secret_key = 'test'
        clients = [
            {'name': 'SYNTHETIC %d' % index, 'file': '/tmp/w.xlsx',
             'written': 1, 'requested': 1, 'failed': [], 'error': None,
             'atp': {'balance': balance, 'monthly': monthly,
                     'months': crs.RECENT_MONTHS}}
            for index, (balance, monthly) in enumerate(figures, start=1)]
        with app_module.app.test_request_context():
            return app_module.render_template(
                'batch_done.html', job={'id': 'jobjobjob'}, clients=clients,
                months=crs.RECENT_MONTHS, is_lite=False, built=len(clients),
                written=len(clients), can_retry=False, error=None, missing=0,
                limits=[])
    return render


def payment_row(paid, when, receipt='000001'):
    return {'detail': 'FINE', 'amount': '500.00', 'paid': paid,
            'paidDate': when, 'receipt': receipt, 'tender': 'CSH'}


def a_case(rows=()):
    return {'id': '00000  FECR000000', 'financials': list(rows)}


def built(rows, cases=(), as_of=CLINIC):
    """A real CRS workbook with those rows written, and what it reported."""
    workbook = load_workbook(FULL)
    sheet = workbook['CASE DATA']
    for offset, values in enumerate(rows):
        row = crs.FIRST_CASE_ROW + offset
        cells = dict({'A': '00000  FECR000000', 'B': 'SYNTHETIC',
                      'D': FRESH, 'G': 'GTR'}, **values)
        for column, value in cells.items():
            sheet[column + str(row)] = value
    figures = actions.build_action_sheet(workbook, list(cases), len(rows),
                                         as_of, 'SYNTHETIC CLIENT')
    return workbook, figures


class TestWhatItReports:
    def test_the_balance_is_what_is_owed(self):
        _, figures = built([{'J': 250, 'K': 100}])
        assert figures['balance'] == "$350.00"

    def test_across_every_case(self):
        _, figures = built([{'J': 250}, {'J': 100.50}, {'J': 40}])
        assert figures['balance'] == "$390.50"

    def test_the_monthly_figure_is_what_has_been_going_in(self):
        """Six payments of $500 over the last year, which the calculator wants
        as a rate rather than a total."""
        cases = [a_case([payment_row('500.00', '%02d/15/2026' % month,
                                     receipt='00000%d' % month)
                         for month in range(1, 7)])]
        _, figures = built([{'J': 250}], cases=cases)
        assert figures['monthly'] == "$250.00"
        assert figures['months'] == crs.RECENT_MONTHS

    def test_a_workbook_with_no_cases_still_answers(self):
        _, figures = built([])
        assert figures['balance'] == "$0.00"


class TestTheZeroItRefusesToSay:
    def test_no_itemized_payments_is_not_a_zero(self):
        """ICOS not publishing an itemization looks exactly like nobody ever
        paying, and the calculator would treat the two the same. A hearing
        where the client is recorded as having paid nothing on a debt they have
        been paying is the whole thing going the wrong way."""
        _, figures = built([{'J': 250}], cases=[a_case()])
        assert figures['monthly'] is None

    def test_and_the_page_says_so_in_words(self, done_page):
        page = done_page(monthly=None)
        assert "itemized no payments" in page
        assert "$0.00 a month" not in page
        assert "not proof nothing was paid" in page


class TestItAgreesWithTheWorkbook:
    def test_the_balance_is_the_action_list_total(self):
        """Two numbers for the same thing is one number that is wrong. The
        page reads from what built the sheet rather than working it out again,
        and this is the test that keeps it that way."""
        workbook, figures = built([{'J': 250}, {'K': 1000.25}])
        assert figures['balance'] == "${:,.2f}".format(
            workbook['ACTION LIST']['B4'].value)

    def test_the_monthly_figure_is_the_payment_sheet_average(self):
        cases = [a_case([payment_row('120.00', '03/15/2026'),
                         payment_row('120.00', '04/15/2026', receipt='000002')])]
        workbook, figures = built([{'J': 250}], cases=cases)
        total = workbook['PAYMENTS']['B2'].value
        assert figures['monthly'] == "${:,.2f}".format(
            Decimal(total) / crs.RECENT_MONTHS)


class TestItSurvivesTheBuild:
    def test_a_real_workbook_build_hands_the_figures_back(self, tmp_path):
        """Everything above works on the action sheet in isolation. This is the
        function the jobs actually call, and figures that are worked out and
        then dropped between there and the finish page are worse than none: the
        page simply says nothing and nobody knows why."""
        import tasks

        monkeypatched = tasks.tmp_dir
        tasks.tmp_dir = str(tmp_path) + os.sep
        try:
            path, _, figures, _ = tasks.build_workbook(
                [], 'SYNTHETIC CLIENT', '01/01/1900', False)
        finally:
            tasks.tmp_dir = monkeypatched
        os.unlink(path)

        assert figures is not None
        assert figures['balance'] == "$0.00"
        assert figures['months'] == crs.RECENT_MONTHS


class TestWhatTheStafferSees:
    def test_the_finish_page_carries_both(self, done_page):
        page = done_page(balance="$1,340.00", monthly="$25.00")
        assert "$1,340.00" in page
        assert "$25.00" in page
        assert "ability to pay calculator" in page

    def test_a_clinic_list_carries_one_line_for_each_client(self, batch_page):
        page = batch_page([("$100.00", "$10.00"), ("$220.00", None)])
        assert "$100.00" in page and "$10.00" in page
        assert "$220.00" in page
        assert "no payments itemized" in page

    def test_the_monthly_figure_says_what_it_is(self, done_page):
        """An average of what was recorded, which is not the same as what was
        agreed, and a court asks about the second one. A staffer who reads it
        as the agreed payment enters a number the client never promised."""
        page = done_page(monthly="$25.00")
        assert "average" in page
        assert str(crs.RECENT_MONTHS) in page
        assert "Iowa Courts recorded" in page


class TestWhereItMustNotGo:
    def test_the_figures_stay_off_the_progress_log(self):
        """The progress log is what alert mail carries out of the building.

        How much a client owes the court is their business and nobody else's,
        and the rule the rest of Napier's alerting keeps is that case numbers
        may leave and people may not. A dollar figure attached to a run is a
        person's finances, so it stays on the screen of whoever ran it.
        """
        for name in ('tasks.py', 'actions.py'):
            with open(os.path.join(ROOT, name)) as handle:
                for number, line in enumerate(handle, start=1):
                    if 'job.log(' in line or 'alerts.record(' in line:
                        assert 'atp' not in line, "%s:%d" % (name, number)

    def test_and_out_of_the_job_state_the_browser_polls(self):
        """to_dict is the progress page's view of a run and is served as JSON
        while it is going. The figures live on job.result, which it leaves
        alone, and a run in flight has no business naming the debt anyway."""
        import jobs

        job = jobs.Job('crs')
        job.result = {'atp': {'balance': '$1,000.00', 'monthly': '$25.00'}}
        assert 'atp' not in str(job.to_dict())
        assert '1,000.00' not in str(job.to_dict())
