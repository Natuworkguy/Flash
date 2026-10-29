---
name: analyse-data
description: Analyse a spreadsheet, CSV, JSON or database and report what it shows
---
1. Look before computing: read the first lines, or a quick script printing the columns, row count, types and a few rows.
2. Write a Python script and run it with shell. Use the standard library's csv and statistics for small jobs; pandas (pip install pandas) for anything bigger. SQLite files: python's sqlite3.
3. Check data quality: missing values, duplicates, odd units and dates. Say what you cleaned.
4. Compute what answers the question, and sanity-check the totals.
5. Present: the answer first in a sentence, then a chart or stats block with the real numbers, or a table. For an interactive dashboard, build a web page.

Never invent numbers. Keep the script, so the analysis can be re-run.

## Examples

```
read {"path": "sales.csv", "limit": 10}
```

```
write {"path": "analyse.py", "content": "import pandas as pd\ndf = pd.read_csv('sales.csv', parse_dates=['date'])\nprint(df.groupby(df.date.dt.to_period('M')).revenue.sum())\n"}
```

```
shell {"command": "python analyse.py"}
```
