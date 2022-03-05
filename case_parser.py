from bs4 import BeautifulSoup
import platform
import datetime
import time

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
        non_party_designations = [
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
            'OBLIGOR', 
            'PAYOR', 
            'TRUSTEE', 
            'WITNESS', 
            'JUVENILE - MOTHER OF', 
            'JUVENILE - FATHER OF',
            'ATTORNEY'
            ]
        if (case['role'] in non_party_designations):
            print("Supressing non-party case")
            continue
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
    charge_code_dict = {
        "GUILTY": "GTR",
        "GUILTY BY COURT": "GTR",
        "GUILTY - NEGOTIATED/VOLUN PLEA": "GPL",
        "CONVERT TO SIMPLE MISDEM": "GPL",
        "ACQUITTED": "ACQ",
        "DISMISSED": "DISM",
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
