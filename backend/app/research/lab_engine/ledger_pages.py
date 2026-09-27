"""CSV export for indexed source disclosures."""
import csv
import io

CSV_FIELDS = ('politician', 'tradeDate', 'filedDate', 'firstPublicAt', 'owner', 'account',
              'tickerReported', 'priceSymbol', 'asset', 'transactionType', 'amountRange',
              'source', 'exclusion')







def csv_bytes(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(CSV_FIELDS)
    for row in rows:
        # Neutralize spreadsheet formulas while retaining the original in JSON evidence.
        values = [str(row.get(k) or '') for k in CSV_FIELDS]
        writer.writerow(["'" + v if v.lstrip().startswith(('=', '+', '-', '@')) else v for v in values])
    return output.getvalue().encode('utf-8')
