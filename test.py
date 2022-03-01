from bs4 import BeautifulSoup
from decimal import Decimal
from openpyxl import load_workbook
from os import listdir
from pprint import pprint
import crs
import case_parser
import platform

tmp_dir = '/tmp/'
if platform.system() == 'Windows':
    tmp_dir = '.\\tmp\\'

wb = load_workbook('CRS 3.5.0 blank.xlsx')
ws = wb['CASE DATA']

case_ids = []
files = listdir(tmp_dir)
for f in files:
    if f.endswith(".html") and f != "search_results.html":
        case_ids.append(f.split("_")[0])
case_ids = list(set(case_ids))
print len(case_ids)

row = 4
for case_id in case_ids:
    case = {'id': case_id}
    #print "Collecting " + case['id']

    with open(tmp_dir + case['id'] + "_summary.html", "r") as text_file:
        case_parser.parse_case_summary(text_file.read(), case)

    with open(tmp_dir + case['id'] + "_charges.html", "r") as text_file:
        case_parser.parse_case_charges(text_file.read(), case)

    with open(tmp_dir + case['id'] + "_financials.html", "r") as text_file:
        case_parser.parse_case_financials(text_file.read(), case)

    print "===", row - 3, "of", len(case_ids), "==="
    pprint(case)
    #crs.process_case(case, ws, row)
    row += 1


#wb.save("CRS 3.4.1_test.xlsx")
        