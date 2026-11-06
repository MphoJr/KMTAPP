from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_excel_download_fix.py')

if not APP.exists():
    raise SystemExit('app.py not found. Put this file in KomatiAssetApp next to app.py.')

text = APP.read_text(encoding='utf-8')
pattern = re.compile(r'def excel_report\(sheets\):.*?(?=\n# -----------------------------------------------------------------------------|\nst\.title\(|\n\ndef )', re.S)

replacement = '''def excel_report(sheets):
    """Create a safe multi-sheet Excel report."""
    output = io.BytesIO()
    used_names = set()

    def safe_sheet_name(value):
        # Excel forbids: \\ / * ? : [ ] and limits titles to 31 characters.
        name = re.sub(r'[\\\\/*?:\\[\\]]', '_', str(value)).strip().strip("'")
        if not name:
            name = "Sheet"
        name = name[:31]

        base = name
        suffix = 1
        while name.lower() in used_names:
            suffix_text = f"_{suffix}"
            name = f"{base[:31-len(suffix_text)]}{suffix_text}"
            suffix += 1

        used_names.add(name.lower())
        return name

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, df in sheets.items():
            worksheet_name = safe_sheet_name(name)
            df.to_excel(
                writer,
                index=False,
                sheet_name=worksheet_name,
            )

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for cells in worksheet.columns:
                width = min(
                    max(len(str(cell.value or "")) for cell in cells) + 2,
                    45,
                )
                worksheet.column_dimensions[cells[0].column_letter].width = width

    output.seek(0)
    return output.getvalue()

'''

match = pattern.search(text)
if not match:
    raise SystemExit('Could not locate excel_report() in app.py. No changes made.')

updated = text[:match.start()] + replacement + text[match.end():]
compile(updated, str(APP), 'exec')
shutil.copy2(APP, BACKUP)
APP.write_text(updated, encoding='utf-8')

print('SUCCESS: Excel download repaired.')
print(f'Backup created: {BACKUP.name}')
print('Invalid Excel worksheet characters will now be replaced with underscores.')
