import re

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from openpyxl.styles import Alignment # Added import
from openpyxl.styles import Font, PatternFill
from zoneinfo import ZoneInfo

# Iowa keeps one time zone, and this app runs on a dyno that keeps UTC. Every
# date in a workbook is a court date read against a clinic day: the twenty year
# cut on the SOL sheet, the two and eight year expungement waits, whether a
# probation term is still running. A run at nine at night in Des Moines was
# stamped tomorrow and measured every one of those waits a day early.
#
# Verified on the napier-dev dyno on 2026-08-01: date.today() answered
# 2026-08-02 while Iowa was still on the 1st.
IOWA = ZoneInfo('America/Chicago')


def iowa_now():
    """The moment, where the client and the courthouse are."""
    return datetime.now(IOWA)


def iowa_today():
    """The day in Iowa, which is the day the clinic is having."""
    return iowa_now().date()

MISMATCH_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
MISMATCH_FONT = Font(color="9C0006")

# Where the CRS template stops, counted in cases rather than rows. Napier writes
# CASE DATA from row 4 down and never stops, and Excel accepts every row it is
# given, so outgrowing the template costs a number rather than raising anything:
# the analysis sheets carry formulas only so far, and their totals sum a shorter
# range still. Nobody has hit these on a real client, but the failure is a
# quietly low figure carried into a hearing, so the run says something.
#
# tests/test_workbook_limits.py reads these back out of the two .xlsx files, so
# a new template that moves them fails the suite instead of going unnoticed.
FIRST_CASE_ROW = 4
# CASE DATA row 1 totals =SUM(x4:x300).
CASE_DATA_TOTAL_LIMIT = 297
# SOL, BANKRUPTCY and EXEMPTIONS each total =SUM(x4:x100). Full CRS only; the
# Lite template does not have those sheets.
ANALYSIS_TOTAL_LIMIT = 97
# The first sheet to run out of rows altogether: SOL's formulas stop at row 150.
ANALYSIS_ROW_LIMIT = 147
# In the Lite template that is EXPUNGEMENT & 910.7, whose rows are offset by one
# and stop at row 200, so its last case is CASE DATA row 201.
LITE_ANALYSIS_ROW_LIMIT = 198


def workbook_limits(written, is_lite):
    """What this many cases outgrows in the CRS template, worst first."""
    warnings = []
    if written > CASE_DATA_TOTAL_LIMIT:
        warnings.append(
            "CASE DATA's totals on row 1 only add up the first %d cases, so "
            "every total in this workbook is short."
            % CASE_DATA_TOTAL_LIMIT)
    row_limit = LITE_ANALYSIS_ROW_LIMIT if is_lite else ANALYSIS_ROW_LIMIT
    if written > row_limit:
        warnings.append(
            "The analysis sheets have rows for the first %d cases only, so the "
            "cases after that are on CASE DATA and nowhere else."
            % row_limit)
    if not is_lite and written > ANALYSIS_TOTAL_LIMIT:
        warnings.append(
            "The totals on SOL, BANKRUPTCY and EXEMPTIONS only add up the "
            "first %d cases. The rows below that are right, the totals are "
            "low." % ANALYSIS_TOTAL_LIMIT)
    return warnings

charge_code_map = {
    "GUILTY": {"GTR":1},
    "GUILTY BY COURT": {"GTR":1},
    "GUILTY - NEGOTIATED/VOLUN PLEA": {"GPL":1},
    "CONVERT TO SIMPLE MISDEM": {"GPL":1},
    "ACQUITTED": {"ACQ":0},
    "DISMISSED": {"DISM":0},
    "DISMISSED BY COURT": {"DISM":0},
    "DISMISSED BY OTHER": {"DISM":0},
    "DEFERRED": {"DEF":2},
    "NOT GUILTY": {"ACQ":0},
    "WAIVED TO ADULT COURT": {"JWV":0},
    "ADJUDICATED": {"JUV":1},
    "WITHDRAWN": {"WTHD":0},
    "NOT FILED": {"NOTF":0},
    "CIVIL": {"CIV":0}
}

# The rank for a disposition string charge_code_map has never seen. It sits
# between a dismissal and a conviction on purpose.
#
# It used to be 3, above everything else here. So one unrecognised word on one
# count of a case demoted the whole case to OTH, and OTH is not GTR, GPL or DEF,
# which is the test four analysis sheets run before they say anything:
# LICENSE-REGIS in 897 formulas, BANKRUPTCY in 396, EXEMPTIONS in 394, SOL in
# 294. A client with a conviction and one stray code came out of all four
# looking like a client with no conviction at all, and the workbook gave no sign
# of it.
#
# Below a conviction now, so an unknown code cannot talk over one Napier
# understands. Still above a dismissal, so a case whose counts are all
# unrecognised reads as OTH rather than passing itself off as dismissed. Both
# are guesses, which is why an unrecognised code is also named on its own row
# and mailed out while the run is happening instead of being absorbed quietly.
OTH_RANK = 0.5

# What column V says about that row. The workbook outlives the alert and gets
# read by whoever has the client in front of them, so the guess has to be
# visible in the file itself and not only in Alex's inbox.
UNKNOWN_DISPOSITION_NOTE = (
    "Iowa Courts recorded a disposition Napier does not recognise (%s), so this "
    "case is coded OTH. The expungement, bankruptcy, exemption and licence "
    "sheets treat OTH as no conviction. Check this case in ICOS before relying "
    "on what they say about it."
)


# Column H of CASE DATA, headed "Vehicular?". LICENSE-REGIS reads it in 299
# formulas and it is the whole of the difference between the two answers that
# sheet can give: convicted with debt and H="YES" is "License & registration",
# anything else is "Registration only". Napier never wrote the column, so the
# sheet has never once said "License & registration" about anybody. Blank is not
# neutral there, it is a quiet "no".
#
# Iowa suspends a driver's licence for unpaid debt on a chapter 321 conviction
# and holds vehicle registration for delinquent court debt of any kind, which is
# the distinction the sheet is drawing. So the test is the chapter the statute
# sits in, which Napier already has: it is the adjudicated statutory code in
# column F.
# A city or county prosecuting a traffic offence under its own ordinance puts
# the ordinance citation in the adjudicated code, and ICOS prints that instead of
# the state section. Nine of 90 captured cases are charged that way and every one
# of them is a motor vehicle matter: speeding, seat belts, parking, driving an
# unregistered vehicle. The chapter test used to anchor at the start of the
# string, so all nine read as not a chapter 321 matter at all.
#
# They come in two shapes and only one of them is answerable.
#
# DU/32-321.285(d)(3) is Dubuque adopting the state speeding section by number,
# and the state section is right there in the code. So the chapter is looked for
# at the start of the code or straight after an ordinance citation's / or -,
# which finds it in both shapes and still will not match a chapter that merely
# ends in those digits.
VEHICULAR_CHAPTER = re.compile(r'(?:^\s*|[-/])321[A-Z]?\.', re.I)
# MA/62.01(120)-0198 is the other shape: a bare municipal code section. 62.01 in
# one city's code has nothing to do with 62.01 in another's and neither has
# anything to do with the state chapters, so there is no chapter in it to read.
ORDINANCE_CITATION = re.compile(r'^[A-Z]{2}/', re.I)
# Homicide and serious injury by vehicle live in chapter 707 rather than 321 and
# revoke a licence on conviction, so they are vehicular for this purpose even
# though the chapter does not say so.
VEHICULAR_SECTIONS = ('707.6A',)

# The three codes LICENSE-REGIS treats as a conviction. On anything else it says
# "Z - Neither license nor registration" and never looks at column H at all.
LICENCE_DISPOSITIONS = frozenset({'GTR', 'GPL', 'DEF'})

# Leaving column H blank is honest but it is not visible: LICENSE-REGIS reads a
# blank exactly as it reads "NO" and prints "Registration only", which is a
# sentence about the client's driving licence that nobody checked. So on the rows
# where that sentence actually gets printed, the row says why.
ORDINANCE_VEHICULAR_NOTE = (
    "Column H is blank because Iowa Courts give the adjudicated charge as %s, a "
    "city or county ordinance citation with no state chapter in it, so Napier "
    "cannot tell whether this is a chapter 321 offence. LICENSE-REGIS reads the "
    "blank as \"Registration only\". If the ordinance mirrors a chapter 321 "
    "offence the licence is at stake too, so check this one before advising on "
    "it."
)

# The row has one disposition date and the case had several. Column D now holds
# the one that goes with the code in column G, which is the pairing the sheets
# assume, but the SOL sheet runs its twenty year test off that single date and
# will apply it to every dollar on the row including debt from a count disposed
# years earlier.
DISPOSITION_SPREAD_NOTE = (
    "Counts on this case were disposed on different dates (%s). Column D holds "
    "%s, the date of the disposition in column G. The SOL sheet applies its "
    "20 year test to that one date for the whole row, so if this case is near "
    "the 20 year line check the counts separately."
)


def is_vehicular(statutes):
    """"YES", "NO", or None when there is nothing to judge from.

    None matters. A civil case writes "n/a" into column F and a case whose only
    counts were dismissed writes nothing at all, and in neither is there a
    conviction for a licence to hang off. Saying "NO" there would be asserting
    something Napier does not know, and the sheet reads a blank and a "NO"
    identically anyway, so the honest answer costs nothing.

    A bare municipal ordinance citation is the same situation and used to get a
    confident "NO". It is a section number in one city's code and says nothing
    about which state chapter, if any, the offence answers to.

    Any vehicular count carries the case. A client who pleaded to OWI and a
    drugs count has a licence problem regardless of which count the CRS picked
    to speak for the case in column G.
    """
    if not statutes:
        return None
    codes = [code.strip() for code in str(statutes).split(';')]
    codes = [code for code in codes if code and code.lower() != 'n/a']
    if not codes:
        return None
    for code in codes:
        if VEHICULAR_CHAPTER.search(code):
            return "YES"
        if any(code.upper().startswith(section) for section in VEHICULAR_SECTIONS):
            return "YES"
    if any(ORDINANCE_CITATION.match(code) for code in codes):
        return None
    return "NO"


# How far back to look when working out what somebody is paying now. A court
# asking whether a person can pay wants the recent record, not an average that
# a garnishment in 2003 drags upwards.
RECENT_MONTHS = 12


def _money(text):
    """A dollar figure off an ICOS page, or None when there is not one."""
    if text is None:
        return None
    text = str(text).replace('$', '').replace(',', '').strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def payments(case):
    """Every payment ICOS records on a case, oldest first.

    The itemization has carried a date, a receipt number and a tender type
    against each paid line for as long as Napier has been fetching it, and
    nothing has ever read them. A fee paid in instalments gets a row per
    instalment, so this is a payment history rather than a list of fees.

    Third-party collection fees are excluded the same way they are excluded
    from the fee columns: ICOS lists them and does not count them in the case
    total, so counting a payment against one as money paid on the case would
    overstate what the client has actually put in.
    """
    history = []
    last_detail = None
    for row in case.get('financials') or []:
        detail = (row.get('detail') or '').strip()
        if detail:
            last_detail = detail
        if is_excluded_fee(detail or last_detail or ''):
            continue
        paid = _money(row.get('paid'))
        when = parse_us_date(row.get('paidDate'))
        if paid is None or paid <= 0 or when is None:
            continue
        history.append({
            'date': when,
            'amount': paid,
            'receipt': row.get('receipt'),
            'tender': row.get('tender'),
            'detail': detail or last_detail,
        })
    history.sort(key=lambda payment: payment['date'])
    return history


def payment_history(case, as_of):
    """What a case's payment record says, or None when there is no record.

    None and a record of zero payments are different answers and the sheet
    should not have to guess which it is looking at. A case ICOS has no
    itemization for cannot tell us the client never paid.
    """
    history = payments(case)
    if not history:
        return None
    total = sum((payment['amount'] for payment in history), Decimal(0))
    first, last = history[0]['date'], history[-1]['date']
    recent = sum((payment['amount'] for payment in history
                  if payment['date'] > _add_term(as_of, -RECENT_MONTHS, 'Month')),
                 Decimal(0))
    # Over the window the client was actually paying, not over the age of the
    # case. Somebody who paid steadily for a year and then lost the job has a
    # monthly figure of what they paid, and the gap is reported separately.
    months = max(1, _months_between(first, last) + 1)
    return {
        'count': len(history),
        'total': total,
        'first': first,
        'last': last,
        'monthly': (total / months).quantize(Decimal('0.01')),
        'recent': recent,
        'recent_monthly': (recent / RECENT_MONTHS).quantize(Decimal('0.01')),
        'months_since_last': _months_between(last, as_of),
        'tenders': sorted({payment['tender'] for payment in history
                           if payment['tender']}),
    }


def _months_between(start, end):
    """Whole months from start to end, never negative."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


# Sentence types ICOS uses that put somebody under supervision in the community.
# All three are court-ordered, run for a stated term, and appear in the sentence
# table with that term, which is what makes them answerable from ICOS at all.
#
# PRISON, JAIL and the suspended variants are deliberately not here. Somebody in
# custody is not what the expungement sheet's 910.7 column is asking about, and
# the day they get out is not on this page.
SUPERVISION_SENTENCES = frozenset({
    'PROBATION',
    'DRUG COURT',
    'RESIDENTIAL FACILITY',
})

# ICOS writes a term as a count and a unit, and has used only two units across
# every case we have looked at. The others are here so a term Napier has not
# seen is measured rather than ignored.
DURATION = re.compile(r'^\s*(\d+)\s*(Year|Month|Week|Day)', re.I)

SUPERVISION_NOTE = (
    "Column I says YES because ICOS shows a %s term of %s imposed %s, which on "
    "paper runs to %s. Napier reads the sentence table and cannot see a term "
    "discharged early, a term extended, or anyone on parole, so confirm this "
    "before relying on it."
)


def _add_term(start, count, unit):
    """The day a term of `count` `unit`s beginning on `start` runs out.

    Calendar arithmetic rather than a multiple of 365, because a five year
    probation term imposed on a leap day is not five times 365 days long and
    the answer here decides what goes in a column about somebody's debt.
    """
    unit = unit.lower()
    if unit == 'day':
        return start + timedelta(days=count)
    if unit == 'week':
        return start + timedelta(weeks=count)
    months = count * 12 if unit == 'year' else count
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # The 31st of a month landing on a 30 day month, and 29 February landing on
    # a common year, both fall back to that month's last day.
    day = min(start.day, monthrange(year, month)[1])
    return date(year, month, day)


def parse_us_date(text):
    """MM/DD/YYYY as ICOS writes it, or None for anything else."""
    if not text:
        return None
    try:
        return datetime.strptime(str(text).strip(), '%m/%d/%Y').date()
    except ValueError:
        return None


def supervision_term(sentences):
    """The supervision term that runs longest, as (type, duration, start, end).

    None when the case has no supervision sentence, or has one with no date or
    no stated term, because a term nobody can put an end date on cannot answer
    the question the column is asking.
    """
    longest = None
    for sentence in sentences or []:
        if (sentence.get('type') or '').strip().upper() not in SUPERVISION_SENTENCES:
            continue
        start = parse_us_date(sentence.get('date'))
        if start is None:
            continue
        match = DURATION.match(str(sentence.get('duration') or ''))
        if not match:
            continue
        end = _add_term(start, int(match.group(1)), match.group(2))
        if longest is None or end > longest[3]:
            longest = (sentence['type'].strip().upper(),
                       sentence['duration'].strip(), start, end)
    return longest


def is_under_supervision(sentences, as_of):
    """("YES", term) when a supervision term is still running, else (None, None).

    Never "NO". A blank and a "NO" read the same to the expungement sheet, so
    writing "NO" would buy nothing and would claim something Napier cannot know:
    ICOS records no discharge when probation ends early, records an extension
    inconsistently, and does not carry parole at all, since parole is corrections
    rather than the court. So this answers the one direction it can evidence,
    which is that a term was imposed and has not run out yet.
    """
    term = supervision_term(sentences)
    if term is None or term[3] < as_of:
        return None, None
    return "YES", term


def get_dominant_charge(charges):
    """Pick the one disposition code that represents the whole case.

    ICOS lists a disposition per count, so a plea deal shows up as a guilty
    alongside several dismissals. The CRS has one column for it, and the
    ranking in charge_code_map decides which count speaks for the case.

    Returns a copy. The caller's charge keeps its list of dispositions, so
    calling this twice on the same case gives the same answer both times.

    The copy carries 'unknown_dispositions', the ICOS wordings that produced an
    OTH. Empty on almost every case. When it is not, the case is coded on a
    guess and the caller is the one that has to say so.
    """
    if len(charges) == 0:
        return None
    delisted = dict(charges[0])
    raw_charge = delisted['disposition']
    raw_dates = delisted.get('disposition_dates') or []
    charge_dict = {}
    # Which counts produced each code, by date, so the winning code can be
    # paired with the date of the count that actually produced it.
    dates_by_code = {}
    unknown = set()
    for index, disposition in enumerate(raw_charge):
        disposition = disposition.replace("DNU-", "")
        if not disposition:
            # ICOS printed no adjudication for this count. That is the absence
            # of a disposition, and it used to be recorded as NOTF, NOT FILED,
            # which is a specific thing that was not true of any of the three
            # real cases it landed on: one filed eleven days earlier and still
            # open, one ICOS dismissed in 2021, one ICOS closed in 1993.
            #
            # It is not a harmless label. The EXPUNGEMENT sheet's DISM ACQ?
            # column reads NOTF as dismissed or acquitted and answers YES,
            # eligible under 901C.2, so an open charge was reported as
            # expungeable. Nothing is recorded here instead, which is the
            # workbook's own way of saying it: SOL, BANKRUPTCY and EXEMPTIONS
            # each render column G as IF(G=0, "open charge", G).
            continue
        elif disposition not in charge_code_map:
            charge_dict["OTH"] = OTH_RANK
            unknown.add(disposition)
            charge_key = "OTH"
        else:
            charge_key, rank = next(iter(charge_code_map[disposition].items()))
            charge_dict[charge_key] = rank
        if index < len(raw_dates) and raw_dates[index]:
            dates_by_code.setdefault(charge_key, []).append(raw_dates[index])
    sorted_tuples = sorted(charge_dict.items(), reverse=True,
                           key=lambda item: item[1] if item[1] is not None else float('inf'))
    delisted['disposition'] = sorted_tuples[0][0] if sorted_tuples else ''
    delisted['unknown_dispositions'] = sorted(unknown)

    # Column D is the date of the disposition column G names, and on a case
    # where the counts were disposed on different days those two used to come
    # from different counts. The parser handed over whichever date came first on
    # the page and column G is chosen by rank, so a case pleaded out on one
    # count and adjudicated on another more than a year later reported the later
    # code against the earlier date.
    #
    # That pairing is not cosmetic. The SOL sheet asks IF(D+7300 < today) on
    # every row, the twenty year limit on enforcing an Iowa judgment, and it
    # sorts the row's debt into time barred or "NO ARGUMENT" on the answer.
    # Taking the earliest date available makes a judgment look older than it is,
    # so the error ran toward telling an attorney a debt had aged out when it
    # had not.
    #
    # Where several counts share the winning code, the last of them is the day
    # the case finished reaching that disposition, and it is also the reading
    # that will not retire a debt early.
    winning_dates = dates_by_code.get(delisted['disposition']) or []
    if winning_dates:
        delisted['dispositionDate'] = max(
            winning_dates, key=lambda d: parse_us_date(d) or date.min)

    # A row that compresses counts disposed on different days is saying less
    # than the case does, and the sheet that reads the date cannot see the
    # spread. Recorded here, said in column V by the caller.
    spread = sorted({d for d in raw_dates if d})
    delisted['disposition_date_spread'] = spread if len(spread) > 1 else []
    return delisted


def case_level_code(status):
    """The CRS code for ICOS's case-level status, or None if it is not one.

    The status ICOS prints on the case summary is its own vocabulary. Across 90
    captured pages it reads GUILTY PLEA/DEFAULT, VIOLATIONS HANDLED BY CLERK,
    DISMISSED, BY TRIAL TO COURT, OTHER JUDGMENT, CLOSED, TRANSFERRED and SMALL
    CLAIM-DISPOSED BY CLERK, and it overlaps the per-count adjudication wordings
    at DISMISSED and nowhere else.

    Only the overlap is read. The rest are not translated here, because whether
    VIOLATIONS HANDLED BY CLERK is a guilty plea is a question about Iowa
    practice rather than about parsing, and five sheets key formulas on the
    answer. An unrecognised status returns None and travels out through
    unknown_dispositions, which already puts the wording in column V and tells
    the run, so the case is visibly uncoded rather than quietly miscoded.
    """
    entry = charge_code_map.get((status or "").replace("DNU-", "").strip())
    return next(iter(entry)) if entry else None


def get_finance_column(detail):
    if "COLLECTION BY CO ATTY" in detail:
        return "P" # UNKNOWN
    if "DELINQUENT REVOLVING FUND" in detail:
        return "P" # UNKNOWN
        
    if "FINE" in detail:
        return "R" # FINE
    if "DEFERRED JUDGMENT CIVIL PENALTY" in detail:
        return "R" # FINE
    if "INFRACTIONS-PENALTIES AND FORFEITURES-CITY" in detail:
        return "R" # FINE
    if "NONSCHEDULED CHAPTER 321" in detail:
        return "R" # FINE
    if "SCHEDULED VIOLATION/NON-SCHEDULED" in detail:
        return "R" # FINE
    
    #if "FILING" in detail:
    #    return "J" # FILING
    #if "COURT COSTS" in detail:
    #    return "J" # FILING
    #if "TRAFFIC/SIMP MISD APPEAL FEES" in detail:
    #    return "J" # FILING
    #if "OTHER SIMPLE MISDEMEANORS" in detail:
    #    return "J" # FILING

    if "INDIGENT DEFENSE" in detail:
        return "J" # INDIGENT DEFENSE

    if "SURCHARGE" in detail:
        return "Q" # SURCHARGE

    if "ROOM/BOARD" in detail:
        return "L" # JAIL / ROOM & BOARD

    if "RESTITUTION" in detail:
        return "S" # RESTITUTION

    if "THIRD PARTY" in detail:
        return "K" # LINEBARGER COLLECTION FEE

    if "REVENUE" in detail:
        return "K" #DEPARTMENT OF REVENUE COLLECTION FEE

    if "SHERIFF" in detail:
        return "M" # SHERIFF

    if "PROBATION" in detail:
        return "N" # PROBATION REVOCATION FEE

    return "O" # MISC

ICOS_BUCKETS = ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER')

# Rows the summary counts as COSTS. Everything the summary does not put in one
# of the four named buckets falls into OTHER, so only COSTS needs listing.
COSTS_MARKERS = (
    'SHERIFF', 'INDIGENT DEFENSE', 'COURT COSTS', 'FILING', 'CLERK',
    'WITNESS', 'JURY', 'SERVICE', 'ROOM/BOARD', 'ATTORNEY FEE',
    'TRANSCRIPT', 'APPEAL FEES', 'DEPOSITION',
    # Both of these read like OTHER and are not. Polk County put $5.27 of
    # postage in COSTS and $37.00 of city/county misc fees in COSTS, in its own
    # summary, on pages where the itemization was otherwise exact. Guessing
    # wrong here is cheap and self-announcing: the bucket stops adding up and
    # the row falls back to category totals, which is where both of these
    # already were.
    'POSTAGE', 'MISC FEES BY CITY/COUNTY',
)

FINE_MARKERS = (
    'FINE', 'DEFERRED JUDGMENT CIVIL PENALTY',
    'INFRACTIONS-PENALTIES AND FORFEITURES-CITY',
    'NONSCHEDULED CHAPTER 321', 'SCHEDULED VIOLATION/NON-SCHEDULED',
)


def get_summary_bucket(detail):
    """Which of the five ICOS summary buckets a line item rolls up into.

    The summary is a rollup, not a fee breakdown, so reconciling a payment back
    to a fee means knowing which bucket each line fed. Getting this wrong is
    caught by the partition check in reconcile_financials rather than producing
    a wrong number, so an unrecognised fee costs us the reconciliation and
    nothing else.
    """
    text = (detail or '').upper()
    if 'SURCHARGE' in text:
        return 'SURCHARGE'
    if 'RESTITUTION' in text:
        return 'RESTITUTION'
    if any(m in text for m in FINE_MARKERS):
        return 'FINE'
    if any(m in text for m in COSTS_MARKERS):
        return 'COSTS'
    return 'OTHER'


def _unique_paid_subset(amounts, target):
    """Indices of the rows that sum to target, when exactly one set does.

    ICOS records payments per bucket, not per line. Where only one combination
    of lines can account for the amount paid, that combination is the answer.
    Where several could, we cannot tell which fee was paid and say so.
    """
    if target == 0:
        return frozenset()
    if target == sum(amounts):
        return frozenset(range(len(amounts)))
    if len(amounts) == 1:
        return frozenset([0]) if amounts[0] == target else None
    if len(amounts) > 18:
        return None  # combinatorially unreasonable; treat as ambiguous

    from itertools import combinations
    found = None
    for size in range(1, len(amounts)):
        for combo in combinations(range(len(amounts)), size):
            if sum(amounts[i] for i in combo) != target:
                continue
            if found is not None:
                return None  # more than one explanation
            found = frozenset(combo)
    return found


def reconcile_financials(case):
    """Per-column amounts owed, keeping the fee breakdown and the ICOS balance.

    The summary reconciles against what ICOS says is due but collapses every fee
    into five buckets. The itemization keeps the fee detail but shows original
    assessments with the Paid column blank, so it overstates anything already
    paid. This partitions the itemization into the buckets the summary reports,
    checks each bucket against its summary original, and then subtracts the
    bucket's payment from the specific lines that account for it.

    Reconciling is per category rather than per case: a category that does not
    add up is reported as a summary total, and the categories that do add up
    still get their fee breakdown.

    Returns (columns, note). columns is None when nothing on the row could be
    reconciled, which means the caller should fall back to the summary. note is
    None when the whole row is fee by fee, and otherwise names the categories
    that are not, either because they did not add up or because their payment
    could not be attributed to particular lines.
    """
    categories = case.get('summary_categories')
    rows = case.get('financials')
    if not categories or not rows:
        return None, None

    summary = {}
    for category in categories:
        if category.get('original') is None:
            return None, None
        summary[category['label'].upper()] = category
    if set(summary) != set(ICOS_BUCKETS):
        return None, None

    # Partition the itemization, carrying the last detail across continuation
    # rows the way itemized_financials does. A continuation row has no detail
    # and no amount, only a payment against the line above it, so its payment
    # is credited to that line rather than dropped.
    buckets = {name: [] for name in ICOS_BUCKETS}
    last_detail = None
    current = None
    for row in rows:
        detail = row.get('detail') or ''
        if is_excluded_fee(detail):
            last_detail = None
            current = None
            continue
        if not detail.strip():
            if last_detail is None:
                continue
            detail = last_detail
        else:
            last_detail = detail
        amount = row.get('amount')
        paid = row.get('paid')
        if amount is None:
            # Continuation row: a further payment against the line above.
            if current is not None and paid is not None:
                current[2] += Decimal(str(paid))
            continue
        current = [detail, Decimal(str(amount)),
                   Decimal(str(paid)) if paid is not None else Decimal(0)]
        buckets[get_summary_bucket(detail)].append(current)

    # A bucket has to add up to what the summary says was assessed before any
    # payment is attributed off it. A fee read into the wrong bucket shows up as
    # a shortfall in one and a matching excess in another, so both fail and
    # neither is quietly wrong, which is why a bucket that does match to the cent
    # is good evidence its partition is right.
    #
    # A bucket that does not match is reported the way summary_financials would
    # report it and the rest of the row keeps its fee breakdown. Giving up on the
    # whole row instead emptied columns J, K and L, and those are where the
    # statute-of-limitations and Polk room-and-board sheets look for twenty year
    # old attorney fee and jail debt. Old cases are both the ones that sheet is
    # for and the ones most likely to have an itemization that will not
    # reconcile, so the two lined up badly.
    columns = {}
    unresolved = []
    unreconciled = []
    carrying = []
    for name in ICOS_BUCKETS:
        entries = buckets[name]
        category = summary[name]
        if entries or category['original']:
            carrying.append(name)

        assessed = sum((entry[1] for entry in entries), Decimal(0))
        if abs(assessed - category['original']) > Decimal('0.01'):
            unreconciled.append(name)
            due = category.get('due')
            if due is None:
                due = category['original'] - (category.get('paid') or Decimal(0))
            if due:
                column = get_finance_column(category['label'])
                columns[column] = columns.get(column, Decimal(0)) + due
            continue

        if not entries:
            continue
        paid = category.get('paid') or Decimal(0)

        # The itemization's own Paid column is blank on most fees, but not on
        # all of them: restitution in particular is paid down in instalments
        # that ICOS lists line by line. Where those line payments account for
        # everything the summary says was paid in this bucket, there is nothing
        # to infer and no ambiguity to flag.
        by_line = sum((entry[2] for entry in entries), Decimal(0))
        if abs(by_line - paid) <= Decimal('0.01'):
            for detail, amount, line_paid in entries:
                owed = amount - line_paid
                if owed <= 0:
                    continue
                column = get_finance_column(detail)
                columns[column] = columns.get(column, Decimal(0)) + owed
            continue

        amounts = [entry[1] for entry in entries]
        settled = _unique_paid_subset(amounts, paid)
        if settled is None:
            # Cannot say which line was paid. The balance still belongs to this
            # bucket, though, so it goes to the column the bucket's lines share
            # and only falls back to MISC when they disagree. Sending a
            # restitution balance to MISC because a partial payment could not be
            # split across instalments was losing the one distinction the
            # spreadsheet most needs to keep.
            unresolved.append(name)
            due = summary[name].get('due')
            if due is None:
                due = sum(amounts, Decimal(0)) - paid
            shared = {get_finance_column(entry[0]) for entry in entries}
            column = shared.pop() if len(shared) == 1 else 'O'
            columns[column] = columns.get(column, Decimal(0)) + due
            continue
        for index, (detail, amount, _line_paid) in enumerate(entries):
            owed = Decimal(0) if index in settled else amount
            if owed == 0:
                continue
            column = get_finance_column(detail)
            columns[column] = columns.get(column, Decimal(0)) + owed

    if carrying and not set(carrying) - set(unreconciled):
        # Nothing on this row reconciled, so what came out is the summary with
        # extra steps. Hand it back to the caller, which says so in one sentence
        # rather than five.
        return None, None

    notes = []
    if unreconciled:
        text = ("%s did not add up against the itemization, so %s "
                "ICOS's category total rather than a per-fee breakdown"
                % (_and_list(unreconciled),
                   "those balances are" if len(unreconciled) > 1
                   else "that balance is"))
        if any(get_finance_column(summary[name]['label']) == 'O'
               for name in unreconciled):
            text += (", and those fees are in MISCELLANEOUS rather than in "
                     "their own columns")
        notes.append(text + ". The rest of the row is fee by fee and the ICOS "
                            "total is still right.")
    if unresolved:
        notes.append(
            "ICOS records payments per category, not per fee. The payment "
            "in %s could not be tied to a specific line, so that balance "
            "is reported as a category total rather than per fee."
            % ", ".join(unresolved))
    return columns, " ".join(notes) or None


def _and_list(names):
    if len(names) == 1:
        return names[0]
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def is_excluded_fee(detail):
    """Fees ICOS lists but does not count toward the balance.

    A third-party (Linebarger) collection fee appears as a line item, yet ICOS
    leaves it out of the case totals entirely -- summing the itemization at face
    value put money in the collection-costs column that the defendant is not
    shown as owing.
    """
    return "THIRD PARTY" in (detail or "").upper()


def summary_financials(case):
    """Per-column amounts owed, taken from the ICOS summary table.

    The summary reflects payments; the itemization does not. Where ICOS breaks
    the balance out by category, that is the number to report.
    """
    columns = {}
    for category in case.get('summary_categories') or []:
        if is_excluded_fee(category['label']):
            continue
        due = category['due']
        if due is None:
            continue
        column = get_finance_column(category['label'])
        columns[column] = columns.get(column, Decimal(0)) + due
    return columns


def itemized_financials(case):
    financials = {}
    col = None
    previous_col = None

    for f in case['financials']:
        detail = f['detail'] or ''
        if is_excluded_fee(detail):
            # Not part of what ICOS says is owed; counting it inflated the
            # collection-costs column.
            previous_col = None
            continue

        if not detail.strip():
            if previous_col is not None:
                col = previous_col
            else:
                continue  # Skip only if we have no previous category
        else:
            # For rows with non-blank details, get new column categorization
            col = get_finance_column(detail)
            previous_col = col

        if col not in financials:
            financials[col] = Decimal(0)

        amount = f['amount'] if f['amount'] is not None else '0'
        paid = f['paid'] if f['paid'] is not None else '0'
        financials[col] += Decimal(amount)
        financials[col] -= Decimal(paid)

    return financials


# What the fee columns on a row are worth, said plainly, for the rows where
# they are not a straight per-fee breakdown. A reconciled row says nothing,
# because a note on every row is a note nobody reads.
SUMMARY_ONLY_NOTE = (
    "Fee columns are ICOS's five summary categories, not a per-fee breakdown. "
    "The itemization could not be reconciled against them, so sheriff, "
    "indigent defense, jail and probation fees are inside MISCELLANEOUS "
    "rather than in their own columns. The ICOS total is still right."
)

ITEMIZED_ONLY_NOTE = (
    "ICOS gave no category summary for this case, so the fee columns come "
    "from the itemization. ICOS leaves the itemization's paid column blank on "
    "most fees, so anything already paid may still be counted here. Treat "
    "these as assessed rather than owed."
)


def process_financials(case, worksheet, row):
    # Reconciling the itemization against the summary is the only path that
    # keeps the fee breakdown and lands on the balance ICOS reports, so it is
    # tried first. The summary alone is right about the total and collapses
    # every fee into five buckets. The itemization alone keeps the fees and
    # overstates whatever has been paid. Both remain as fallbacks, and a row
    # that used one says so, because a sheriff fee that is missing because it
    # was folded into MISCELLANEOUS looks exactly like one that was never
    # charged.
    financials, note = reconcile_financials(case)
    source = 'reconciled'
    if financials is None:
        financials = summary_financials(case)
        source = 'summary'
        note = SUMMARY_ONLY_NOTE
        if not financials:
            financials = itemized_financials(case)
            source = 'itemized'
            note = ITEMIZED_ONLY_NOTE

    total_due = None
    if 'total_due' in case:
        total_due = Decimal(case['total_due'].replace('$', '').replace(',', ''))

    for column, value in financials.items():
        worksheet[column + str(row)] = value
    if total_due is not None:
        worksheet['U' + str(row)] = total_due

    # A caveat about where the fee columns came from is only worth reading if
    # there are fees in them. ICOS reporting nothing due means every fee column
    # on the row is zero however Napier arrived at it, so the caveat describes a
    # breakdown of no money. That is not a rare shape: of the 25 captured pages
    # that carried one of these, 23 owed nothing. Column V is also where the
    # notes that matter go, a disposition Napier had to guess at and a case Iowa
    # Courts would not give up, and a column staff have learned to skip past is
    # worse than an empty one.
    #
    # Only this caveat goes quiet. The mismatch note below still fires on a paid
    # off case, because fee columns adding up to something against a zero
    # balance is a real disagreement and flagging it is that note's whole job.
    nothing_owed = total_due is not None and total_due == 0
    notes = [note] if note and not nothing_owed else []

    # If the per-category figures still don't add up to the balance ICOS
    # reports, the ICOS figure (column U) is the one to trust -- flag the row so
    # staff don't take the categories at face value.
    flagged = False
    if total_due is not None:
        categorized = sum(financials.values(), Decimal(0))
        if abs(categorized - total_due) > Decimal('0.01'):
            flagged = True
            cell_u = worksheet['U' + str(row)]
            cell_u.fill = MISMATCH_FILL
            cell_u.font = MISMATCH_FONT
            notes.append(
                "Category fees total $%s but ICOS shows $%s due (%s figures) - trust "
                "the ICOS total; the difference is usually payments or third-party "
                "collection fees ICOS no longer counts"
                % (categorized, total_due, source)
            )

    if notes:
        cell_v = worksheet['V' + str(row)]
        cell_v.value = " ".join(notes)
        if flagged:
            cell_v.font = MISMATCH_FONT

def append_note(worksheet, row, text):
    """Add to column V without displacing what process_financials put there.

    A row can be both reconciled from the summary and coded on a guess, and the
    two notes are about different things. Whichever is written second joins the
    first rather than replacing it.
    """
    cell = worksheet['V' + str(row)]
    cell.value = ("%s %s" % (cell.value, text)).strip() if cell.value else text


def owes_money(worksheet, row):
    """Whether this row's fee columns add up to anything, read back off the sheet.

    The same SUM(J:S) the analysis sheets take, asked after process_financials
    has written them, so a caveat about what one of those sheets will print only
    goes on a row that sheet is going to print about.
    """
    total = Decimal(0)
    for column in 'JKLMNOPQRS':
        value = worksheet[column + str(row)].value
        if value in (None, ''):
            continue
        try:
            total += Decimal(str(value))
        except InvalidOperation:
            continue
    return total > 0


def process_case(case, worksheet, row, as_of=None):
    """Write one case into CASE DATA. Returns the dispositions it could not read.

    Almost always empty. When it is not, the case is on the sheet under a code
    Napier guessed at, and the return value is how the run gets to say so.

    as_of is the clinic date, the same one build_workbook puts in BASIC INFO B3.
    Whether a probation term is still running is only answerable against a day,
    and it has to be that day rather than today, so that reopening a workbook
    next year does not silently change what column I said.
    """
    if as_of is None:
        as_of = iowa_today()
    i = str(row)
    worksheet['A' + i] = case['id']
    worksheet['B' + i] = case['county']
    charge = get_dominant_charge(case['charges'])
    ordinance_note = None

    cell_E = worksheet['E' + i] # Get cell E

    if charge is None:
          worksheet['C' + i] = case['summary_created_date']
          worksheet['D' + i] = case['summary_disposition_date']
          # come back later and do this with a map / dictionary
          description_text = ""
          if case['id'][7:9]=="DR":
              description_text = "Domestic relations [civil] - " + case['summary_dispo_status']
          elif case['id'][7:9]=="DA":
              description_text = "Domestic abuse [civil] - " + case['summary_dispo_status']
          elif case['id'][7:9]=="SC":
              description_text = "Small claims - " + case['summary_dispo_status']
          elif case['id'][7:9]=="PC":
              description_text = "post conviction relief - " + case['summary_dispo_status']
          else:
              description_text = "other civil - " + case['summary_dispo_status']
          cell_E.value = description_text
          worksheet['F' + i] = "n/a"
          worksheet['G' + i] = "CIV"
          process_financials(case, worksheet, i)
          # return # This was here, but we want to apply alignment, so moved it down
    else:
        description = charge['description']
        disposition = charge['disposition']
        disposition_date = charge['dispositionDate']

        if not disposition:
            # No count on this case has been adjudicated. ICOS still prints a
            # status and a date for the case as a whole, and Napier parsed both
            # off the summary page and then used them only on the civil path.
            #
            # The date is the one that hurts. A blank column D is how this
            # workbook detects an open case: the EXPUNGEMENT sheet's POSSIBLE
            # PENDING CHARGES column counts case rows that have no disposition
            # date. So a case ICOS closed in 1993 and one it dismissed in 2021
            # were both being counted as live charges hanging over the client,
            # and a pending charge is what blocks expungement under 901C.2.
            # Filling it from the summary is also what keeps that column honest
            # in the other direction: the genuinely pending case has no date on
            # the summary either, so it stays blank and still counts.
            description = description or charge.get('original_description') or ''
            disposition_date = disposition_date or case.get(
                'summary_disposition_date') or ''
            status = (case.get('summary_dispo_status') or '').strip()
            if status:
                code = case_level_code(status)
                if code is None:
                    charge.setdefault('unknown_dispositions', [])
                    if status not in charge['unknown_dispositions']:
                        charge['unknown_dispositions'] = sorted(
                            charge['unknown_dispositions'] + [status])
                else:
                    disposition = code

        worksheet['C' + i] = charge['offenseDate']
        worksheet['D' + i] = disposition_date
        cell_E.value = description
        worksheet['F' + i] = charge['charge']
        worksheet['G' + i] = disposition
        # Only when we can tell. is_vehicular returns None for a case with no
        # adjudicated statute, and the cell is left alone rather than told "NO".
        vehicular = is_vehicular(charge['charge'])
        if vehicular is not None:
            worksheet['H' + i] = vehicular
        elif charge['charge'] and disposition in LICENCE_DISPOSITIONS:
            # Held until the fees are in, because LICENSE-REGIS only prints its
            # verdict on a case that owes something and this note is only worth
            # reading where that verdict appears.
            ordinance_note = ORDINANCE_VEHICULAR_NOTE % charge['charge']

    # Outside the charge branch on purpose. The sentence table is read off the
    # charges page whatever get_dominant_charge made of the adjudications, and a
    # case can carry a supervision term that Napier coded as something other
    # than a plain conviction.
    supervised, term = is_under_supervision(case.get('sentences'), as_of)
    if supervised is not None:
        worksheet['I' + i] = supervised
        supervision_note = SUPERVISION_NOTE % (
            term[0].lower(), term[1], term[2].strftime('%m/%d/%Y'),
            term[3].strftime('%m/%d/%Y'))
    else:
        supervision_note = None

    cell_E.alignment = Alignment(wrap_text=True) # Apply text wrapping

    # If charge was None, we still need to process financials if it wasn't returned early
    if charge is None:
        # process_financials(case, worksheet, i) # Already called above if charge is None
        if supervision_note:
            append_note(worksheet, i, supervision_note)
        return [] # Now we can return

    process_financials(case, worksheet, i)

    # After process_financials, which owns column V, so the note joins whatever
    # it wrote about the money rather than being overwritten by it.
    if supervision_note:
        append_note(worksheet, i, supervision_note)
    if ordinance_note and owes_money(worksheet, i):
        append_note(worksheet, i, ordinance_note)
    spread = charge.get('disposition_date_spread') or []
    if spread and owes_money(worksheet, i):
        append_note(worksheet, i, DISPOSITION_SPREAD_NOTE
                    % (", ".join(spread), disposition_date))
    unknown = charge.get('unknown_dispositions') or []
    if unknown:
        append_note(worksheet, i,
                    UNKNOWN_DISPOSITION_NOTE % ", ".join(unknown))
    return unknown
