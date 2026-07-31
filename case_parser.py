from bs4 import BeautifulSoup
from decimal import Decimal, InvalidOperation
from werkzeug.utils import secure_filename
import os
import platform
import re
#import datetime
from datetime import *
import time

tmp_dir = '/tmp/'
if platform.system() == 'Windows':
    tmp_dir = '.\\tmp\\'

def _dump(name, html):
    """Write a scraped page to tmp_dir, but only when asked to.

    These pages are the unredacted court record for a named person. The
    search dump holds every name and date of birth the search matched, and
    a case dump holds one defendant's charges and finances. Writing them
    was unconditional, so a production dyno accumulated privileged client
    data on local disk for as long as it stayed up, for nobody's benefit.
    They are genuinely useful when working on the parsers, so the capability
    stays and the default flips: off unless NAPIER_DUMP_HTML is set.

    Names are built from case ids, which come from scraped HTML and request
    forms, so they are sanitized here and cannot escape tmp_dir.
    """
    if not os.environ.get('NAPIER_DUMP_HTML'):
        return
    os.makedirs(tmp_dir, exist_ok=True)
    with open(os.path.join(tmp_dir, secure_filename(name)), 'w') as text_file:
        text_file.write(html)

def _cell_words(cell):
    """A table cell's words with ICOS's padding taken out.

    The date of birth two columns to the left of the role arrives wrapped in
    CRLFs and tabs on the one real search page we have, and the role cell on
    that same row happens to arrive clean. Matching a role against a list of
    exact strings works only for as long as that stays true, and the day it
    stops, every non-party role passes the check at once and nothing says so.
    """
    return ' '.join(cell.get_text().split())


# Roles ICOS gives someone who is on a case without the case being theirs. Every
# other role is treated as the person searched for, so this list decides whose
# convictions and whose court debt end up in a client's record summary, and it
# has been wrong by omission four separate times.
NON_PARTY_ROLES = frozenset({
    'NOT ATTORNEY',
    'NOT JUDGE',
    'ADMINISTRATOR',
    'APPLICANT',
    'ATTORNEY AND GUARDIAN-AD-LITEM',
    'ATTORNEY FOR APPELLANT',
    'ATTORNEY FOR APPELLEE',
    'ATTORNEY FOR CHILD',
    'ATTORNEY FOR CSRU',
    'ATTORNEY FOR DEFENDANT',
    'ATTORNEY FOR FATHER',
    'ATTORNEY FOR MOTHER',
    'ATTORNEY FOR PARENT',
    'ATTORNEY FOR PETITIONER',
    'ATTORNEY FOR PLAINTIFF',
    'ATTORNEY FOR PROBATE',
    'ATTORNEY FOR RESPONDENT',
    'ATTORNEY - LIMITED APPEARANCE',
    'ATTORNEY OTHER',
    'CONSERVATOR',
    'COUNTER DEFENDANT',
    'COUNTER PLAINTIFF',
    'COUNTY ATTORNEY',
    'CROSS DEFENDANT',
    'CROSS PLAINTIFF',
    'CUSTODIAN - LEGAL',
    'DECEASED INDIVIDUAL',
    'EXECUTOR',
    'FILING AGENT FOR PLAINTIFF',
    'FILING AGENT FOR DEFENDANT',
    'GUARDIAN',
    'GUARDIAN-AD-LITEM',
    'GUARDIAN/CONSERVATOR',
    'INTERPRETER',
    'INTERPRETOR',
    'INTERVENOR',
    'JUDGE',
    'LIEN FILER',
    'NAME OF TRUST',
    # Someone who filed a document into a case they are not party to,
    # which is why the charges and the money on that page belong to
    # somebody else. The charge parser takes every row on the page,
    # since in the ordinary case the person searched for is the
    # defendant and there is nothing to separate out, so a case
    # reaching it under this role puts a stranger's convictions in the
    # client's record summary. The 'NO ACCESS' half is the filer's
    # access level to the case file rather than part of the role, so
    # both spellings are listed.
    'NO ACCESS NONPARTY FILER',
    'NONPARTY FILER',
    'OBLIGOR',
    'OBLIGEE',
    'PAYOR',
    'PAYEE',
    'TRUSTEE',
    'WARD',
    'WITNESS',
    'WITNESS – PLAINTIFF',
    'WITNESS - PLAINTIFF',
    'WITNESS - PLAINTIFF',
    'WITNESS – DEFENSE',
    'WITNESS - DEFENSE',
    'JUVENILE - MOTHER OF',
    'JUVENILE - FATHER OF',
    'ATTORNEY',
    'INTERESTED PARTY'
})


# Roles we have actually seen on the person the search is about. Nothing branches
# on this: it is only how a run notices that NON_PARTY_ROLES has a gap, by way of
# the alert in tasks.search_task. Kept deliberately narrow, because a role missing
# from here costs one email and a role missing from NON_PARTY_ROLES costs a
# stranger's record in a client's file.
KNOWN_PARTY_ROLES = frozenset({
    'DEFENDANT',
    'PRO SE DEFENDANT',
    'DEFENDANT - PRO SE',
    'PLAINTIFF',
    'PRO SE PLAINTIFF',
    'PETITIONER',
    'PRO SE PETITIONER',
    'RESPONDENT',
    'PRO SE RESPONDENT',
    'APPELLANT',
    'PRO SE APPELLANT',
    'APPELLEE',
    'PRO SE APPELLEE',
})

# The notice ICOS shows when a name matches more cases than it will list. The
# number is captured rather than assumed, because the only thing worse than not
# knowing the list is short is telling someone the wrong count.
_TOO_MANY = re.compile(r"query\s+returned\s+more\s+than\s+(\d+)\s+records", re.I)


def _flat_text(soup):
    """The page's words with ICOS's padding taken out.

    Every cell on an ICOS page is wrapped in a font tag and padded with CRLFs
    and tabs, which is visible in the date of birth cells of the one real
    search page we have. Anything that matches this page against an exact
    string is matching the padding as well as the words.
    """
    words = [text for text in soup.strings
             if text.parent.name not in ('script', 'style')]
    return re.sub(r'\s+', ' ', ' '.join(words).replace(u'\xa0', u' '))


def truncation_limit(soup):
    """The record limit ICOS stopped at, or None if it listed everything.

    A search that silently comes back short is the same failure as a workbook
    that is quietly missing two cases: the answer looks complete and is not.

    This used to be an exact match against one text node. That was rewritten
    on the reasoning that ICOS pads every cell with CRLFs and tabs, so an
    exact match would never fire on the real page. A truncated page has since
    been captured and that reasoning was wrong: ICOS puts the notice in its
    own cell with no padding, and the old exact match would have caught it.
    The rewrite stands on what is left, which is that it survives a change of
    case, of padding, of the number, or of the markup around the words, and
    the exact match survived none of those.
    """
    found = _TOO_MANY.search(_flat_text(soup))
    if not found:
        return None
    try:
        return int(found.group(1))
    except ValueError:
        return None


def parse_search(html):
    html = html.decode('utf-8', errors='ignore')
    _dump("search_results.html", html)
    soup = BeautifulSoup(html, 'html.parser')
    limit = truncation_limit(soup)
    too_many_results = limit is not None
    if too_many_results:
        print("Iowa Courts stopped listing at %d records" % limit)
    cases = []
    for row in soup.find_all('tr'):
        cols = row.find_all('td')
        if len(cols) != 6:
            continue
        case = {
            'id': list(cols[0].stripped_strings)[0].replace(u'\xa0', u' '),
            'title': cols[2].string,
            'name': cols[3].string.strip(),
            # stripped like 'name' above; ICOS pads every cell with
            # CRLFs and tabs, and a caller comparing to a real date loses.
            'dob': cols[4].string.replace(u'\xa0', u'').strip(),
            'role': _cell_words(cols[5])
        }
        if case['id'] == 'Case ID':
            continue
        if any([case['id'] == c['id'] for c in cases]):
            print("Supressing duplicate case id", case['id'])
            continue
        if (case['role'] in NON_PARTY_ROLES):
            print("Supressing non-party case")
            continue
        cases.append(case)
    return (cases, too_many_results)

def parse_case_summary(html, case):
    html = html.decode('utf-8', errors='ignore')
    _dump(case['id'] + "_summary.html", html)
    soup = BeautifulSoup(html, 'html.parser')
    case['county'] = soup.find_all('tr')[2].find_all('td')[0].string
    case['summary_created_date'] = soup.find_all('tr')[2].find_all('td')[1].string
    try:
        case['summary_disposition_date'] = soup.find_all('tr')[4].find_all('td')[1].string
    except IndexError:
        print("IndexError while trying to find disposition date. May be pending case.")
        case['summary_disposition_date'] = ''
    case['summary_dispo_status'] = soup.find_all('tr')[4].find_all('td')[0].string
    if case['summary_dispo_status'] is None:
        case['summary_dispo_status'] = ""

def parse_case_charges(html, case):
    html = html.decode('utf-8', errors='ignore')
    _dump(case['id'] + "_charges.html", html)
    soup = BeautifulSoup(html, 'html.parser')
    charges = []
    charge_list = list()
    cur_charge = None
    prev_od_dd = prev_od_mm = prev_od_yyyy = None
    new_od_dd = new_od_mm = new_od_yyyy = None
    cur_section = None
    prior_charge = str()
    prior_description = str()
    #disposition = {}
    charge_code_dict = {
        "GUILTY": "GTR",
        "DNU-GUILTY": "GTR",
        "GUILTY BY COURT": "GTR",
        "GUILTY - NEGOTIATED/VOLUN PLEA": "GPL",
        "CONVERT TO SIMPLE MISDEM": "GPL",
        "ACQUITTED": "ACQ",
        "DISMISSED": "DISM",
        "DNU-DISMISSED": "DISM",
        "DISMISSED BY COURT": "DISM",
        "DISMISSED BY OTHER": "DISM",
        "DEFERRED": "DEF",
        "NOT GUILTY": "ACQ",
        "WAIVED TO ADULT COURT": "JWV",
        "ADJUDICATED": "JUV",
        "WITHDRAWN": "WTHD",
        "NOT FILED": "NOTF",
        "CIVIL": "CIV"
    }
    rows = soup.find_all('tr')
    for row in rows:
        cols = row.find_all('font')
        texts = [
            ''.join(col.find_all(text=True))
                .replace(u'\xa0', u' ')
                .replace('\r', '')
                .replace('\n', '')
                .replace('\t', '')
                .strip()
            for col in cols
        ]

        if len(texts) == 0:
            continue
        if texts[0].startswith("Count"):
            cur_charge = {}
            cur_section = "Charge"
        if texts[0] == "Adjudication":
            cur_section = "Adjudication"
        if texts[0] == "Sentence":
            cur_section = "Sentence"
        if texts[0].startswith("Parties"):
            cur_section = "Parties"


        if cur_section == "Charge":
            if len(texts) >= 3 and texts[0].startswith("Offense Date:"):
                if 'prior_offenseDate' not in vars():
                    cur_charge['offenseDate'] = texts[1]
                    prior_offenseDate = cur_charge['offenseDate'] 
                else:
                    prev_od_mm, prev_od_dd, prev_od_yyyy = prior_offenseDate.split('/')
                    new_od_mm, new_od_dd, new_od_yyyy = texts[1].split('/')
                    if date(int(new_od_yyyy), int(new_od_mm), int(new_od_dd)) > date(int(prev_od_yyyy), int(prev_od_mm), int(prev_od_dd)):
                        cur_charge['offenseDate'] = prior_offenseDate
                    else: 
                        cur_charge['offenseDate'] = texts[1]
                        prior_offenseDate = cur_charge['offenseDate'] 

        if cur_section == "Parties":
            if len(texts) >= 1 and texts[0].startswith("Title:"):
                case['name'] = texts[0].split(" vs ")[1]
            if len(texts) >= 2 and texts[1] == "DEFENDANT":
                case['dob'] = texts[2]
                cur_section = ""

        if cur_section == "Adjudication":
            if len(texts) >= 4 and texts[0].startswith("Charge:"):
                cur_charge['charge'] = prior_charge+texts[1]
                prior_charge = cur_charge['charge']+";"
                cur_charge['description'] = prior_description+texts[3]
                prior_description = cur_charge['description']
            
            if len(texts) >= 4 and texts[0].startswith("Adjudication:"):
                charge_list.insert(0, texts[1])
                cur_charge['disposition'] = charge_list
                prior_description = prior_description + "[" + charge_code_dict.get(texts[1], "OTH") + "];"
                cur_charge['description'] = cur_charge['description'] + "[" + charge_code_dict.get(texts[1], "OTH") + "]"
                if 'prior_dispositionDate' not in vars():
                    cur_charge['dispositionDate'] = texts[3]
                    prior_dispositionDate = cur_charge['dispositionDate']
                else:
                    cur_charge['dispositionDate'] = prior_dispositionDate

        
    if cur_charge is not None:
        if ";" not in cur_charge['description']:
            cur_charge['description'] = cur_charge['description'][:cur_charge['description'].index("[")] 
            #print("Disposition: " + charge_code_dict.get(cur_charge['disposition'][0], "OTH"))
            disp_code = charge_code_dict.get(cur_charge['disposition'][0], "OTH")
            if disp_code in ["WITHD", "DISM", "ACQ", "NOTF"]:
                cur_charge['charge'] = ""
        else:
            cleaned_list = [] 
            filter_charge_string = cur_charge['charge']
            filter_description_string = cur_charge['description']
            filter_charge_list = filter_charge_string.split(";")
            filter_description_list = filter_description_string.split(";")
            combined_list = list(zip(filter_charge_list, filter_description_list))
            for index, charge_tuple in enumerate(combined_list):
                if any(x in charge_tuple[1] for x in ["WITHD", "DISM", "ACQ", "NOTF"]):
                    #print("Excluding: " + charge_tuple[0] + " " + charge_tuple[1])
                    pass
                else: 
                    #print("Including: " + charge_tuple[0] + " " + charge_tuple[1])
                    cleaned_list.append(charge_tuple[0])
            #print("Cleaned Charge List: " + ';'.join(cleaned_list))
            cur_charge['charge'] = ';'.join(cleaned_list)
        charges.append(cur_charge)
        
    case['charges'] = charges

def parse_money(text):
    """A dollar figure from an ICOS cell, or None if the cell isn't one.

    ICOS writes blanks, "N/A" and "0.00" in the same columns as real amounts,
    so callers need to tell "no figure here" from "zero".
    """
    if text is None:
        return None
    cleaned = text.replace(u'\xa0', u' ').strip().replace('$', '').replace(',', '')
    if not cleaned or cleaned.upper() == 'N/A':
        return None
    negative = cleaned.startswith('(') and cleaned.endswith(')')
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_financial_summary(soup):
    """The top-of-page summary: per-category (original, paid, due) plus the total.

    This summary is what ICOS itself treats as owed. The itemization below it
    lists original assessments only -- payments and the third-party collection
    fee ICOS excludes from the balance show up here and nowhere else -- so this
    is the authoritative source for what a defendant actually owes.

    Parsed by shape rather than by fixed column offsets: a row carrying a label
    plus three figures is a category, and a row with three figures and no label
    is the total.
    """
    table = soup.find('table', {'id': 'one_col'})
    if table is None:
        return None, []

    total_due = None
    categories = []
    for row in table.find_all('tr'):
        cells = [c.get_text().replace(u'\xa0', u' ').strip() for c in row.find_all('td')]
        amounts = [parse_money(c) for c in cells]
        figures = [a for a in amounts if a is not None]
        labels = [c for c, a in zip(cells, amounts) if c and a is None and c.upper() != 'N/A']
        if len(figures) < 3:
            continue
        original, paid, due = figures[-3], figures[-2], figures[-1]
        if labels:
            categories.append({
                'label': labels[0],
                'original': original,
                'paid': paid,
                'due': due,
            })
        elif total_due is None:
            total_due = due

    return total_due, categories


def parse_case_financials(html, case):
    html = html.decode('utf-8', errors='ignore')
    _dump(case['id'] + "_financials.html", html)
    soup = BeautifulSoup(html, 'html.parser')

    # Extract the summary from the top half of the page
    total_due, categories = parse_financial_summary(soup)
    case['summary_categories'] = categories
    if total_due is not None:
        case['total_due'] = "$%s" % total_due

    # Extract the financial details from the bottom half of the page
    financials = []
    rows = soup.find('form').find_all('tr')
    for row in rows:
        cols = row.find_all('td')
        if cols[1].string == 'Detail':
            continue
        financials.append({
            'detail': cols[1].string,
            'amount': cols[4].string,
            'paid': cols[5].string,
            'paidDate': cols[6].string
        })
    case['financials'] = financials


