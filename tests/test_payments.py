"""The payment history Napier has been downloading and throwing away.

Every case number here is 00000 FECR000000 and every date is in 1900, because
the repository is public and a real Iowa case number plus a real payment date
is a person.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs

CLINIC = date(2026, 7, 31)


def _row(detail, paid, when, receipt='000001', tender='CSH', amount='100.00'):
    return {'detail': detail, 'amount': amount, 'paid': paid, 'paidDate': when,
            'receipt': receipt, 'tender': tender}


def _case(rows):
    return {'id': '00000  FECR000000', 'financials': rows}


class TestMoney:
    def test_reads_a_plain_figure(self):
        assert crs._money('45.10') == Decimal('45.10')

    def test_strips_dollars_and_commas(self):
        assert crs._money('$1,234.56') == Decimal('1234.56')

    def test_blank_is_not_zero(self):
        # A fee with nothing in the paid column has not been paid nothing, it
        # has not been reported, and the two have to stay distinguishable.
        assert crs._money('') is None
        assert crs._money('   ') is None
        assert crs._money(None) is None

    def test_nonsense_is_none_rather_than_an_exception(self):
        assert crs._money('see receipt') is None


class TestPayments:
    def test_reads_a_payment(self):
        history = crs.payments(_case([_row('FINE', '25.00', '01/02/1900')]))
        assert len(history) == 1
        assert history[0]['amount'] == Decimal('25.00')
        assert history[0]['date'] == date(1900, 1, 2)
        assert history[0]['receipt'] == '000001'
        assert history[0]['tender'] == 'CSH'

    def test_a_fee_with_no_payment_is_not_a_payment(self):
        assert crs.payments(_case([_row('FINE', '', '')])) == []

    def test_a_zero_payment_is_not_a_payment(self):
        assert crs.payments(_case([_row('FINE', '0.00', '01/02/1900')])) == []

    def test_a_payment_with_no_date_is_dropped(self):
        # Every use of a payment is a use of when it happened, so one without a
        # date would only ever land in a total and skew the monthly figure.
        assert crs.payments(_case([_row('FINE', '25.00', '')])) == []

    def test_oldest_first_whatever_order_icos_listed_them(self):
        history = crs.payments(_case([
            _row('FINE', '3.00', '03/01/1900'),
            _row('FINE', '1.00', '01/01/1900'),
            _row('FINE', '2.00', '02/01/1900'),
        ]))
        assert [payment['amount'] for payment in history] == [
            Decimal('1.00'), Decimal('2.00'), Decimal('3.00')]

    def test_a_continuation_row_is_credited_to_the_fee_above_it(self):
        # ICOS leaves the detail cell blank when a fee was paid in instalments.
        history = crs.payments(_case([
            _row('COURT COSTS', '5.00', '01/01/1900'),
            _row('', '5.00', '02/01/1900'),
        ]))
        assert [payment['detail'] for payment in history] == [
            'COURT COSTS', 'COURT COSTS']

    def test_third_party_collection_fees_are_left_out(self):
        # Same rule the fee columns use: ICOS lists them and does not count
        # them, so a payment against one is not money paid on the case.
        history = crs.payments(_case([
            _row('THIRD PARTY COLLECTION FEE', '50.00', '01/01/1900'),
            _row('FINE', '10.00', '02/01/1900'),
        ]))
        assert [payment['detail'] for payment in history] == ['FINE']

    def test_a_continuation_of_an_excluded_fee_is_also_left_out(self):
        history = crs.payments(_case([
            _row('THIRD PARTY COLLECTION FEE', '50.00', '01/01/1900'),
            _row('', '50.00', '02/01/1900'),
        ]))
        assert history == []


class TestJudgmentsAreNotPayments:
    """A civil money judgment sits in the same itemization table as the court
    fees, and ICOS marks it satisfied with a journal entry, so it read to
    Napier exactly like a fee somebody had paid off. On the captured corpus
    that was 99.5% of the payment history: money one private party owed
    another, reported as the client's record of paying the clerk."""

    def test_the_judgment_line_is_not_money_paid_on_the_case(self):
        history = crs.payments(_case([
            _row('JUDGMENTS', '5000.00', '01/01/1900', amount='5000.00'),
            _row('FINE', '10.00', '02/01/1900'),
        ]))
        assert [payment['detail'] for payment in history] == ['FINE']

    def test_a_continuation_of_a_judgment_is_left_out_too(self):
        history = crs.payments(_case([
            _row('JUDGMENTS', '2500.00', '01/01/1900', amount='5000.00'),
            _row('', '2500.00', '02/01/1900', amount='5000.00'),
        ]))
        assert history == []

    def test_the_filing_fee_for_one_is_still_a_fee(self):
        # CONFESSION OF JUDGMENT - $5000 OR MORE A-R is what the clerk charges
        # to file it. Matching the word rather than the line would have thrown
        # away real court debt.
        history = crs.payments(_case([
            _row('CONFESSION OF JUDGMENT - $5000 OR MORE A-R', '185.00',
                 '01/01/1900'),
        ]))
        assert len(history) == 1

    def test_the_civil_penalty_is_still_a_fine(self):
        history = crs.payments(_case([
            _row('DEFERRED JUDGMENT CIVIL PENALTY', '65.00', '01/01/1900'),
        ]))
        assert len(history) == 1

    def test_is_judgment_wants_the_whole_line(self):
        assert crs.is_judgment('JUDGMENTS')
        assert crs.is_judgment('  judgments  ')
        assert not crs.is_judgment('CONFESSION OF JUDGMENT - $5000 OR MORE A-R')
        assert not crs.is_judgment('DEFERRED JUDGMENT CIVIL PENALTY')
        assert not crs.is_judgment('')
        assert not crs.is_judgment(None)


class TestJudgmentsAreStillReported:
    """Taking them out of the payment history must not throw them away. A
    client being garnished on a judgment has less money for court debt, which
    is the whole of an ability-to-pay argument."""

    def test_it_finds_the_judgment(self):
        found = crs.judgments(_case([
            _row('JUDGMENTS', '5000.00', '01/01/1900', amount='5000.00'),
            _row('FINE', '10.00', '02/01/1900'),
        ]))
        assert len(found) == 1
        assert found[0]['amount'] == Decimal('5000.00')
        assert found[0]['satisfied'] == Decimal('5000.00')
        assert found[0]['date'] == date(1900, 1, 1)

    def test_the_amount_is_the_judgment_not_what_was_paid_on_it(self):
        found = crs.judgments(_case([
            _row('JUDGMENTS', '100.00', '01/01/1900', amount='5000.00')]))
        assert found[0]['amount'] == Decimal('5000.00')
        assert found[0]['satisfied'] == Decimal('100.00')

    def test_one_judgment_against_six_people_is_one_judgment(self):
        # ICOS lists it once per debtor: six rows, six receipt numbers, one
        # amount between them. Adding them up is how a real $656,285.88
        # judgment in the corpus became $3,937,715.28.
        rows = [_row('JUDGMENTS', '', '01/01/1900', receipt='84800%d' % n,
                     amount='5000.00')
                for n in range(1, 7)]
        found = crs.judgments(_case(rows))
        assert len(found) == 1
        assert found[0]['amount'] == Decimal('5000.00')

    def test_two_judgments_on_different_days_are_two_judgments(self):
        found = crs.judgments(_case([
            _row('JUDGMENTS', '', '01/01/1900', amount='5000.00'),
            _row('JUDGMENTS', '', '02/01/1900', amount='5000.00'),
        ]))
        assert len(found) == 2

    def test_an_unsatisfied_judgment_still_counts(self):
        found = crs.judgments(_case([
            _row('JUDGMENTS', '', '01/01/1900', amount='5000.00')]))
        assert len(found) == 1
        assert found[0]['satisfied'] == Decimal(0)

    def test_a_case_with_no_judgment_has_none(self):
        assert crs.judgments(_case([_row('FINE', '10.00', '01/01/1900')])) == []


class TestPaymentHistory:
    def test_no_itemization_is_none_not_zero(self):
        # A case ICOS publishes no itemization for cannot tell us the client
        # never paid, and the sheet should not have to guess which it is.
        assert crs.payment_history(_case([]), CLINIC) is None

    def test_totals_and_dates(self):
        history = crs.payment_history(_case([
            _row('FINE', '10.00', '01/01/1900'),
            _row('FINE', '30.00', '01/01/1901'),
        ]), CLINIC)
        assert history['count'] == 2
        assert history['total'] == Decimal('40.00')
        assert history['first'] == date(1900, 1, 1)
        assert history['last'] == date(1901, 1, 1)

    def test_monthly_is_over_the_paying_window_not_the_age_of_the_case(self):
        # Twelve months of paying, thirteen payment months inclusive. A client
        # who paid steadily and then stopped should read as somebody who paid
        # what they paid, with the gap reported separately.
        rows = [_row('FINE', '13.00', '%02d/01/1900' % month)
                for month in range(1, 13)]
        rows.append(_row('FINE', '13.00', '01/01/1901'))
        history = crs.payment_history(_case(rows), CLINIC)
        assert history['count'] == 13
        assert history['total'] == Decimal('169.00')
        assert history['monthly'] == Decimal('13.00')

    def test_a_single_payment_does_not_divide_by_zero(self):
        history = crs.payment_history(
            _case([_row('FINE', '10.00', '01/01/1900')]), CLINIC)
        assert history['monthly'] == Decimal('10.00')

    def test_recent_counts_only_the_last_twelve_months(self):
        history = crs.payment_history(_case([
            _row('FINE', '100.00', '01/01/1900'),
            _row('FINE', '60.00', '01/15/2026'),
        ]), CLINIC)
        assert history['total'] == Decimal('160.00')
        assert history['recent'] == Decimal('60.00')
        assert history['recent_monthly'] == Decimal('5.00')

    def test_a_payment_older_than_the_window_is_not_recent(self):
        history = crs.payment_history(
            _case([_row('FINE', '100.00', '01/01/1900')]), CLINIC)
        assert history['recent'] == Decimal('0')
        assert history['recent_monthly'] == Decimal('0.00')

    def test_months_since_last_payment(self):
        history = crs.payment_history(
            _case([_row('FINE', '5.00', '01/31/2026')]), CLINIC)
        assert history['months_since_last'] == 6

    def test_tenders_are_listed_once_each(self):
        history = crs.payment_history(_case([
            _row('FINE', '1.00', '01/01/1900', tender='CSH'),
            _row('FINE', '1.00', '02/01/1900', tender='CHK'),
            _row('FINE', '1.00', '03/01/1900', tender='CSH'),
        ]), CLINIC)
        assert history['tenders'] == ['CHK', 'CSH']


class TestMonthsBetween:
    def test_same_day_is_zero(self):
        assert crs._months_between(date(1900, 1, 1), date(1900, 1, 1)) == 0

    def test_a_day_short_of_a_month_does_not_count(self):
        assert crs._months_between(date(1900, 1, 15), date(1900, 2, 14)) == 0

    def test_the_day_it_lands_counts(self):
        assert crs._months_between(date(1900, 1, 15), date(1900, 2, 15)) == 1

    def test_across_a_year(self):
        assert crs._months_between(date(1900, 6, 1), date(1901, 6, 1)) == 12

    def test_backwards_is_zero_rather_than_negative(self):
        assert crs._months_between(date(1901, 1, 1), date(1900, 1, 1)) == 0


HEADER_ROW = ('<tr><td>&nbsp;</td><td>Detail</td><td>Payor</td><td>Obligor</td>'
              '<td>Original</td><td>Paid</td><td>Date</td><td>Receipt</td>'
              '<td>Type</td></tr>')
PAID_ROW = ('<tr><td>&nbsp;</td><td>FINE</td><td>SYNTHETIC PAYOR</td>'
            '<td>SYNTHETIC OBLIGOR</td><td>100.00</td><td>25.00</td>'
            '<td>01/02/1900</td><td>\n  000123\t</td><td>CHK</td></tr>')
SHORT_ROW = ('<tr><td>&nbsp;</td><td>FINE</td><td>SYNTHETIC PAYOR</td>'
             '<td>SYNTHETIC OBLIGOR</td><td>100.00</td><td>25.00</td>'
             '<td>01/02/1900</td></tr>')


def _page(*rows):
    return ('<html><body><form><table>%s</table></form></body></html>'
            % ''.join((HEADER_ROW,) + rows)).encode('utf-8')


class TestParser:
    def test_the_receipt_and_tender_come_off_the_page(self):
        case = {'id': '00000  FECR000000'}
        case_parser.parse_case_financials(_page(PAID_ROW), case)
        row = case['financials'][-1]
        assert row['detail'] == 'FINE'
        assert row['paid'] == '25.00'
        # ICOS wraps this one in whitespace on the real page.
        assert row['receipt'] == '000123'
        assert row['tender'] == 'CHK'

    def test_the_header_row_is_not_a_payment(self):
        case = {'id': '00000  FECR000000'}
        case_parser.parse_case_financials(_page(PAID_ROW), case)
        assert len(case['financials']) == 1

    @pytest.mark.parametrize('column', ['receipt', 'tender'])
    def test_a_short_row_costs_the_column_not_the_run(self, column):
        # ICOS has published narrower detail tables before now, and a missing
        # column should cost the receipt number rather than the whole case.
        case = {'id': '00000  FECR000000'}
        case_parser.parse_case_financials(_page(SHORT_ROW), case)
        assert case['financials'][-1][column] is None
        assert case['financials'][-1]['paid'] == '25.00'


class TestCellText:
    def _cell(self, html):
        return BeautifulSoup(html, 'html.parser').find('td')

    def test_padding_is_taken_out(self):
        assert case_parser._text(self._cell('<td>\n  a\tb  </td>')) == 'a b'

    def test_an_empty_cell_is_none_rather_than_blank(self):
        assert case_parser._text(self._cell('<td>&nbsp; </td>')) is None

    def test_a_missing_cell_is_none(self):
        assert case_parser._text(None) is None
