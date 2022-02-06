from bs4 import BeautifulSoup
import platform
import datetime

tmp_dir = '/tmp/'
if platform.system() == 'Windows':
    tmp_dir = '.\\tmp\\'

def parse_search(html):
    with open(tmp_dir + "search_results.html", "w") as text_file:
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
    too_many_results = len(soup.find_all(text="Your query returned more than 200 records.")) > 0
    if too_many_results:
        print("Too Many Results")
    cases = []
    for row in soup.find_all('tr'):
        cols = row.find_all('td')
        if len(cols) != 6:
            continue
        case = {
            'id': list(cols[0].stripped_strings)[0].replace(u'\xa0', u' '),
            'title': cols[2].string,
            'name': cols[3].string.strip(),
            'dob': cols[4].string.replace(u'\xa0', u''),
            'role': cols[5].string
        }
        if case['id'] == 'Case ID':
            continue
        if any([case['id'] == c['id'] for c in cases]):
            print("Supressing duplicate case id", case['id'])
            continue
        non_party_designations = ['PAYOR','INTERPRETOR','WITNESS','JUVENILE - MOTHER OF','JUVENILE - FATHER OF','ATTORNEY','OBLIGOR',]
        if any([case['role'] in non_party_designations for c in cases]):
            print("Supressing non-party case")
            continue
        else:
            cases.append(case)
    return (cases, too_many_results)

def parse_case_summary(html, case):
    with open(tmp_dir + case['id'] + "_summary.html", "w") as text_file:
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
    case['county'] = soup.find_all('tr')[2].find_all('td')[0].string

def parse_case_charges(html, case):
    with open(tmp_dir + case['id'] + "_charges.html", "w") as text_file:
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
    charges = []
    charge_list = list()
    cur_charge = None
    cur_section = None
    prior_charge = str()
    prior_description = str()
    #disposition = {}
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
        

        if cur_section == "Adjudication":
            if len(texts) >= 4 and texts[0].startswith("Charge:"):
                #this does everything backwards, I wonder if there is a better way to to do
                #it that matches the chronological order from ICOS?
                cur_charge['charge'] = texts[1]+prior_charge
                print(cur_charge['charge'])
                prior_charge = ";"+cur_charge['charge']
                cur_charge['description'] = texts[3]+prior_description
                prior_description = ";"+cur_charge['description']
            if len(texts) >= 4 and texts[0].startswith("Adjudication:"):
                charge_list.append(texts[1])
                cur_charge['disposition'] = charge_list
                #cur_charge['disposition'] = texts[1]
                print(cur_charge['disposition'])
                #cur_charge['disposition'] 
                cur_charge['dispositionDate'] = texts[3]
                prior_dispositionDate = cur_charge['dispositionDate']
        
    if cur_charge is not None:
        charges.append(cur_charge)
        
    case['charges'] = charges

def parse_case_financials(html, case):
    with open(tmp_dir + case['id'] + "_financials.html", "w") as text_file:
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
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
