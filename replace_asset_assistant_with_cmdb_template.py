from pathlib import Path
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_cmdb_asset_template.py')
START = '# ASSET_MANAGEMENT_ASSISTANT_V1'
END = '# -----------------------------------------------------------------------------\n# DETAILED RESULTS / EXPORT'
NEW_MARKER = '# KOMATI_CMDB_ASSET_TEMPLATE_V2'

if not APP.exists():
    raise SystemExit('app.py not found. Put this installer next to app.py in KomatiAssetApp.')

text = APP.read_text(encoding='utf-8')
if NEW_MARKER in text:
    raise SystemExit('The CMDB-only Komati Asset Template is already installed.')

start = text.find(START)
end = text.find(END)
if start == -1 or end == -1 or start >= end:
    raise SystemExit('Could not safely locate the previous Asset Management Assistant section. No changes made.')

shutil.copy2(APP, BACKUP)

section = r'''# KOMATI_CMDB_ASSET_TEMPLATE_V2
st.divider()
st.markdown("## 📦 CMDB Asset Template Population")
st.caption(
    "This stage uses only the PC and Monitor CMDB population for the selected Contact Site. "
    "The Local Asset Register is not used in these template calculations."
)

# Combine the already-filtered CMDB status populations created by the app.
_cmdb_frames = []
for _status_name, _status_df in status_groups.items():
    if _status_df is not None and not _status_df.empty:
        _frame = _status_df.copy()
        if "Status Group" not in _frame.columns:
            _frame["Status Group"] = _status_name
        _cmdb_frames.append(_frame)

if _cmdb_frames:
    asset_template_cmdb = pd.concat(_cmdb_frames, ignore_index=True, sort=False)
else:
    asset_template_cmdb = pd.DataFrame()


def _asset_template_column(df, candidates):
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    lowered = {str(col).strip().lower(): col for col in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


_model_col = _asset_template_column(asset_template_cmdb, ["Model", "CMDB Model", "Description"])
_manufacturer_col = _asset_template_column(asset_template_cmdb, ["Manufacturer", "CMDB Manufacturer", "Make"])
_source_col = _asset_template_column(asset_template_cmdb, ["Source", "CMDB Source"])
_class_col = _asset_template_column(asset_template_cmdb, ["Class", "Asset Type"])

if asset_template_cmdb.empty or not _model_col:
    st.warning(
        "No CMDB model information is available for the selected Contact Site. "
        "Confirm that the Model and Status columns were selected correctly."
    )
    preproduction_summary = pd.DataFrame()
    old_asset_summary = pd.DataFrame()
    preproduction_details = pd.DataFrame()
    old_asset_details = pd.DataFrame()
else:
    asset_template_cmdb["Asset Model"] = (
        asset_template_cmdb[_model_col]
        .fillna("Unknown Model")
        .astype(str)
        .str.strip()
        .replace({"": "Unknown Model"})
    )
    asset_template_cmdb["Manufacturer / Make"] = (
        asset_template_cmdb[_manufacturer_col].fillna("").astype(str).str.strip()
        if _manufacturer_col else ""
    )
    asset_template_cmdb["CMDB Source"] = (
        asset_template_cmdb[_source_col].fillna("CMDB").astype(str).str.strip()
        if _source_col else "CMDB"
    )
    asset_template_cmdb["Asset Class"] = (
        asset_template_cmdb[_class_col].fillna("").astype(str).str.strip()
        if _class_col else ""
    )

    # New / pre-production assets: current CMDB Preproduction only.
    preproduction_details = asset_template_cmdb[
        asset_template_cmdb["Status Group"].eq("Preproduction")
    ].copy()

    if preproduction_details.empty:
        preproduction_summary = pd.DataFrame(
            columns=[
                "Asset Name / Model", "Manufacturer", "PC CMDB", "Monitor CMDB",
                "Pre-prod / New Assets Received", "Number Issued",
                "Number in Storeroom", "Contingency Assets", "Asset Issuing Plan"
            ]
        )
    else:
        _preprod_group = (
            preproduction_details
            .groupby(["Asset Model", "Manufacturer / Make", "CMDB Source"], dropna=False)
            .size()
            .unstack(fill_value=0)
            .reset_index()
        )
        _preprod_group.columns.name = None
        _source_columns = [c for c in _preprod_group.columns if c not in ["Asset Model", "Manufacturer / Make"]]
        _preprod_group["Pre-prod / New Assets Received"] = _preprod_group[_source_columns].sum(axis=1)
        _preprod_group["PC CMDB"] = sum(
            (_preprod_group[c] for c in _source_columns if "desktop" in str(c).lower() or "pc" in str(c).lower()),
            start=pd.Series(0, index=_preprod_group.index),
        )
        _preprod_group["Monitor CMDB"] = sum(
            (_preprod_group[c] for c in _source_columns if "monitor" in str(c).lower()),
            start=pd.Series(0, index=_preprod_group.index),
        )
        preproduction_summary = _preprod_group.rename(columns={
            "Asset Model": "Asset Name / Model",
            "Manufacturer / Make": "Manufacturer",
        })[[
            "Asset Name / Model", "Manufacturer", "PC CMDB", "Monitor CMDB",
            "Pre-prod / New Assets Received"
        ]].sort_values(
            ["Pre-prod / New Assets Received", "Asset Name / Model"],
            ascending=[False, True]
        ).reset_index(drop=True)
        preproduction_summary["Number Issued"] = "To be completed later"
        preproduction_summary["Number in Storeroom"] = "To be completed later"
        preproduction_summary["Contingency Assets"] = ""
        preproduction_summary["Asset Issuing Plan"] = ""

    # Old / existing assets: In Service + In Store + Loan + Retired.
    _old_statuses = ["In Service", "In Store", "Loan", "Retired"]
    old_asset_details = asset_template_cmdb[
        asset_template_cmdb["Status Group"].isin(_old_statuses)
    ].copy()

    if old_asset_details.empty:
        old_asset_summary = pd.DataFrame(
            columns=[
                "Asset Name / Model", "Manufacturer", "In Service", "In Store",
                "Loan", "Retired", "Number of Old Assets", "Assets for Re-allocation",
                "Assets for Scrapping", "Scrapping Progress", "Comments"
            ]
        )
    else:
        old_asset_summary = (
            old_asset_details
            .groupby(["Asset Model", "Manufacturer / Make", "Status Group"], dropna=False)
            .size()
            .unstack(fill_value=0)
            .reset_index()
            .rename(columns={
                "Asset Model": "Asset Name / Model",
                "Manufacturer / Make": "Manufacturer",
            })
        )
        old_asset_summary.columns.name = None
        for _status in _old_statuses:
            if _status not in old_asset_summary.columns:
                old_asset_summary[_status] = 0
        old_asset_summary["Number of Old Assets"] = old_asset_summary[_old_statuses].sum(axis=1)
        old_asset_summary = old_asset_summary[[
            "Asset Name / Model", "Manufacturer", "In Service", "In Store",
            "Loan", "Retired", "Number of Old Assets"
        ]].sort_values(
            ["Number of Old Assets", "Asset Name / Model"],
            ascending=[False, True]
        ).reset_index(drop=True)
        old_asset_summary["Assets for Re-allocation"] = ""
        old_asset_summary["Assets for Scrapping"] = ""
        old_asset_summary["Scrapping Progress"] = ""
        old_asset_summary["Comments"] = ""

st.markdown("### 🟡 New / Pre-production Assets")
st.info(
    "Calculation: Selected Contact Site → PC + Monitor CMDB → Status = Preproduction "
    "→ Group by Manufacturer and Model → Count the machines."
)
st.dataframe(preproduction_summary, use_container_width=True, hide_index=True)

if not preproduction_summary.empty:
    _preprod_model = st.selectbox(
        "View the assets behind a Preproduction model count",
        preproduction_summary["Asset Name / Model"].tolist(),
        key="preproduction_template_model",
    )
    st.dataframe(
        preproduction_details[preproduction_details["Asset Model"].eq(_preprod_model)],
        use_container_width=True,
        hide_index=True,
    )

st.markdown("### ♻️ Old / Existing Assets")
st.info(
    "Calculation: Selected Contact Site → PC + Monitor CMDB → In Service, In Store, "
    "Loan and Retired → Group by Manufacturer and Model → Count each status and total."
)
st.dataframe(old_asset_summary, use_container_width=True, hide_index=True)

if not old_asset_summary.empty:
    _old_model = st.selectbox(
        "View the assets behind an Old / Existing model count",
        old_asset_summary["Asset Name / Model"].tolist(),
        key="old_asset_template_model",
    )
    st.dataframe(
        old_asset_details[old_asset_details["Asset Model"].eq(_old_model)],
        use_container_width=True,
        hide_index=True,
    )


def build_komati_asset_template():
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Asset Summary"

    navy = "17365D"
    teal = "00A6A6"
    yellow = "FFF2CC"
    green = "E2F0D9"
    blue = "D9EAF7"
    orange = "FCE4D6"
    gray = "E7E6E6"
    white = "FFFFFF"
    thin_gray = Side(style="thin", color="B7B7B7")

    ws.merge_cells("A1:I1")
    ws["A1"] = f"ASSET MANAGEMENT TEMPLATE - {power_station.upper()}"
    ws["A1"].fill = PatternFill("solid", fgColor=navy)
    ws["A1"].font = Font(color=white, bold=True, size=16)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A2:I2")
    ws["A2"] = f"CMDB Contact Site: {selected_contact_site}"
    ws["A2"].fill = PatternFill("solid", fgColor=teal)
    ws["A2"].font = Font(color=white, bold=True)
    ws["A2"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A4:I4")
    ws["A4"] = "NEW / PRE-PRODUCTION ASSETS"
    ws["A4"].fill = PatternFill("solid", fgColor=yellow)
    ws["A4"].font = Font(bold=True, size=12)

    pre_headers = [
        "Asset Name / Model", "Manufacturer", "PC CMDB", "Monitor CMDB",
        "Pre-prod / New Assets Received", "Number Issued", "Number in Storeroom",
        "Contingency Assets", "Asset Issuing Plan"
    ]
    for col, value in enumerate(pre_headers, 1):
        cell = ws.cell(5, col, value)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(color=white, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    row = 6
    for record in preproduction_summary.to_dict("records"):
        for col, header in enumerate(pre_headers, 1):
            ws.cell(row, col, record.get(header, ""))
        for col in range(1, 6):
            ws.cell(row, col).font = Font(color="008000")  # Imported CMDB values
            ws.cell(row, col).fill = PatternFill("solid", fgColor=green)
        for col in range(6, 10):
            ws.cell(row, col).font = Font(color="0000FF")  # User input
            ws.cell(row, col).fill = PatternFill("solid", fgColor=blue)
        row += 1

    pre_total_row = row
    ws.cell(row, 1, "Total Assets")
    ws.cell(row, 3, f"=SUM(C6:C{row-1})")
    ws.cell(row, 4, f"=SUM(D6:D{row-1})")
    ws.cell(row, 5, f"=SUM(E6:E{row-1})")
    for col in range(1, 10):
        ws.cell(row, col).font = Font(bold=True)
        ws.cell(row, col).fill = PatternFill("solid", fgColor=gray)
    row += 2

    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=11)
    ws.cell(row, 1, "OLD ASSETS / DISPOSAL TRACKING")
    ws.cell(row, 1).fill = PatternFill("solid", fgColor=orange)
    ws.cell(row, 1).font = Font(bold=True, size=12)
    row += 1

    old_headers = [
        "Asset Name / Model", "Manufacturer", "In Service", "In Store", "Loan",
        "Retired", "Number of Old Assets", "Assets for Re-allocation",
        "Assets for Scrapping", "Scrapping Progress", "Comments"
    ]
    for col, value in enumerate(old_headers, 1):
        cell = ws.cell(row, col, value)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(color=white, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    old_header_row = row
    row += 1
    old_start = row

    for record in old_asset_summary.to_dict("records"):
        for col, header in enumerate(old_headers, 1):
            ws.cell(row, col, record.get(header, ""))
        for col in range(1, 8):
            ws.cell(row, col).font = Font(color="008000")
            ws.cell(row, col).fill = PatternFill("solid", fgColor=green)
        for col in range(8, 12):
            ws.cell(row, col).font = Font(color="0000FF")
            ws.cell(row, col).fill = PatternFill("solid", fgColor=blue)
        ws.cell(row, 7, f"=SUM(C{row}:F{row})")
        row += 1

    ws.cell(row, 1, "Total Assets Old")
    for col in range(3, 8):
        letter = chr(64 + col)
        ws.cell(row, col, f"=SUM({letter}{old_start}:{letter}{row-1})")
    for col in range(1, 12):
        ws.cell(row, col).font = Font(bold=True)
        ws.cell(row, col).fill = PatternFill("solid", fgColor=gray)

    issuing_validation = DataValidation(
        type="list",
        formula1='"Planned,In progress,Completed,On hold,Not required"',
        allow_blank=True,
    )
    ws.add_data_validation(issuing_validation)
    if pre_total_row > 6:
        issuing_validation.add(f"I6:I{pre_total_row-1}")

    for data_row in range(old_start, row):
        ws.cell(data_row, 10).number_format = "0%"

    for cells in ws.iter_rows(min_row=5, max_row=row, min_col=1, max_col=11):
        for cell in cells:
            cell.border = Border(bottom=thin_gray)
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    widths = {1: 30, 2: 20, 3: 13, 4: 15, 5: 13, 6: 13, 7: 18, 8: 19, 9: 18, 10: 18, 11: 42}
    for col, width in widths.items():
        ws.column_dimensions[chr(64 + col)].width = width
    ws.freeze_panes = "A5"

    # Evidence sheets.
    def add_dataframe_sheet(name, df):
        safe_name = name[:31]
        evidence_ws = wb.create_sheet(safe_name)
        if df.empty:
            evidence_ws["A1"] = "No records found for this section."
            return
        columns = list(df.columns)
        for col, value in enumerate(columns, 1):
            cell = evidence_ws.cell(1, col, str(value))
            cell.fill = PatternFill("solid", fgColor=navy)
            cell.font = Font(color=white, bold=True)
            cell.alignment = Alignment(wrap_text=True)
        for r_idx, record in enumerate(df.itertuples(index=False, name=None), 2):
            for c_idx, value in enumerate(record, 1):
                evidence_ws.cell(r_idx, c_idx, value)
        evidence_ws.freeze_panes = "A2"
        evidence_ws.auto_filter.ref = evidence_ws.dimensions
        for cells in evidence_ws.columns:
            evidence_ws.column_dimensions[cells[0].column_letter].width = min(
                max(len(str(cell.value or "")) for cell in cells) + 2, 38
            )

    add_dataframe_sheet("Preproduction Details", preproduction_details)
    add_dataframe_sheet("Old Existing Details", old_asset_details)

    breakdown = pd.concat([
        preproduction_summary.assign(Section="New / Preproduction"),
        old_asset_summary.assign(Section="Old / Existing"),
    ], ignore_index=True, sort=False)
    add_dataframe_sheet("Model Status Breakdown", breakdown)

    notes = wb.create_sheet("Population Notes")
    notes_rows = [
        ("Item", "Explanation"),
        ("Power Station", power_station),
        ("Contact Site", selected_contact_site),
        ("Population basis", "PC CMDB and Monitor CMDB only. Local Asset Register excluded at this stage."),
        ("New / Preproduction", "Status Group = Preproduction, grouped by Manufacturer and Model."),
        ("Old / Existing", "Status Group = In Service, In Store, Loan or Retired, grouped by Manufacturer and Model."),
        ("Number Issued / Storeroom", "To be completed during the later Local Register comparison stage."),
        ("Management decisions", "Contingency, issuing plan, re-allocation, scrapping and progress require review and approval."),
    ]
    for r_idx, values in enumerate(notes_rows, 1):
        notes.cell(r_idx, 1, values[0])
        notes.cell(r_idx, 2, values[1])
    notes["A1"].fill = notes["B1"].fill = PatternFill("solid", fgColor=navy)
    notes["A1"].font = notes["B1"].font = Font(color=white, bold=True)
    notes.column_dimensions["A"].width = 28
    notes.column_dimensions["B"].width = 95
    for cells in notes.iter_rows():
        for cell in cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


_template_bytes = build_komati_asset_template()
_safe_station = re.sub(r"[^A-Za-z0-9]+", "_", str(power_station)).strip("_") or "Power_Station"
st.download_button(
    "📥 Download Fully Populated Asset Template",
    data=_template_bytes,
    file_name=f"{_safe_station}_Asset_Template_Populated.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
    key="download_fully_populated_asset_template",
)

st.success(
    "The workbook is populated from the selected Contact Site's CMDB data. "
    "Blue cells remain available for management inputs and later Local Register information."
)

'''

updated = text[:start] + section + text[end:]
compile(updated, str(APP), 'exec')
APP.write_text(updated, encoding='utf-8')

print('SUCCESS: CMDB-only Asset Template population installed.')
print(f'Backup created: {BACKUP.name}')
print('The Local Register is excluded from this stage.')
print('Preproduction is counted by model; old/existing assets are split by In Service, In Store, Loan and Retired.')
print('Next: python -m py_compile app.py')
