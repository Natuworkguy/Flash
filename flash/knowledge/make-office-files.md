---
name: make-office-files
description: Create or edit PDF, Word, Excel or PowerPoint files
---
Write a Python script and run it with shell, installing the library if needed:
- Word (.docx): python-docx. Headings, paragraphs, tables, images.
- Excel (.xlsx): openpyxl. Write values and formulas, set number formats and column widths, freeze the header row.
- PowerPoint (.pptx): python-pptx. One idea per slide.
- PDF: build an HTML page and print it with headless Chromium (playwright: page.pdf()), which gives the best typography; or reportlab / fpdf2 for simple PDFs.

Reading: the read tool extracts text from .pdf and .docx directly.

Check the result before handing it over: check_office_file opens it as the user will (formulas recalculated) and lists formula errors, blank pages, empty slides and placeholder text, with the pages as pictures to look at for text out of its box or a table cut off. Fix the script, make the file again and check again until it is clean. Then show it: send_pdf for PDFs; for other files say where it was saved, and that it was checked.

## Examples

```
shell {"command": "python -m pip install -q python-docx && python make_report.py"}
```

A PDF printed from HTML.
```
shell {"command": "python -c \"from playwright.sync_api import sync_playwright as s; p = s().start(); b = p.chromium.launch(); pg = b.new_page(); pg.goto('file://' + __import__('os').path.abspath('report.html')); pg.pdf(path='report.pdf', print_background=True); b.close(); p.stop()\""}
```

```
check_office_file {"path": "budget.xlsx"}
```

```
send_pdf {"path": "report.pdf", "caption": "Q3 report"}
```
