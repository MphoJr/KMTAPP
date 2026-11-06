from pathlib import Path
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_multi_device_check.py')
MARKER = '# MULTI_INSERVICE_DEVICE_CHECK_V1'

if not APP.exists():
    raise SystemExit('app.py not found. Put this installer next to app.py in KomatiAssetApp.')
text = APP.read_text(encoding='utf-8')
if MARKER in text:
    raise SystemExit('Multi-device In Service check is already installed.')
shutil.copy2(APP, BACKUP)

anchor = '# -----------------------------------------------------------------------------\n# DETAILED RESULTS / EXPORT'
if anchor not in text:
    raise SystemExit('Could not find Detailed Results / Export section. No changes made.')

section = r'''# MULTI_INSERVICE_DEVICE_CHECK_V1
st.divider()
st.markdown("## 👤 In-Service Multi-Device Investigation")
st.caption(
    "Management control: identify people who have more than one CMDB device in In Service "
    "status for the selected Contact Site(s). This is an investigation list, not an automatic correction."
)

# Use the already filtered combined In Service CMDB population.
_multi_inservice = status_groups.get("In Service", pd.DataFrame()).copy()

# Find the best available employee/person identifiers in the CMDB extracts.
_employee_id_col = find_column(
    _multi_inservice.columns,
    ["Employee ID", "Personnel Number", "Employee Number", "Pers No", "User Unique Number"],
)
_username_col = find_column(
    _multi_inservice.columns,
    ["Username", "User Name", "Network ID", "User ID"],
)
_name_col = find_column(_multi_inservice.columns, ["Name", "First Name"])
_surname_col = find_column(_multi_inservice.columns, ["Surname", "Last Name"])

# Build a stable person key: Employee ID first, Username second.
if _employee_id_col:
    _employee_key = _multi_inservice[_employee_id_col].fillna("").astype(str).str.strip().str.upper()
else:
    _employee_key = pd.Series("", index=_multi_inservice.index, dtype=object)

if _username_col:
    _username_key = _multi_inservice[_username_col].fillna("").astype(str).str.strip().str.upper()
else:
    _username_key = pd.Series("", index=_multi_inservice.index, dtype=object)

_multi_inservice["Person Key"] = _employee_key
_use_username = _multi_inservice["Person Key"].eq("") & _username_key.ne("")
_multi_inservice.loc[_use_username, "Person Key"] = _username_key[_use_username]
_multi_inservice["Person Key Used"] = "Employee ID"
_multi_inservice.loc[_use_username, "Person Key Used"] = "Username"
_multi_inservice.loc[_multi_inservice["Person Key"].eq(""), "Person Key Used"] = "No person identifier"

# Human-readable display name when available.
_first = (
    _multi_inservice[_name_col].fillna("").astype(str).str.strip()
    if _name_col else pd.Series("", index=_multi_inservice.index)
)
_last = (
    _multi_inservice[_surname_col].fillna("").astype(str).str.strip()
    if _surname_col else pd.Series("", index=_multi_inservice.index)
)
_multi_inservice["Person"] = (_first + " " + _last).str.strip()
_multi_inservice.loc[_multi_inservice["Person"].eq(""), "Person"] = _multi_inservice["Person Key"]

# Only people with a usable person identifier can be evaluated reliably.
_person_rows = _multi_inservice[_multi_inservice["Person Key"].ne("")].copy()
_person_counts = _person_rows.groupby("Person Key").size().rename("In Service Device Count")
_multi_person_keys = _person_counts[_person_counts > 1].index
_multi_device_details = _person_rows[_person_rows["Person Key"].isin(_multi_person_keys)].copy()

if _multi_device_details.empty:
    _multi_device_summary = pd.DataFrame(
        columns=[
            "Person Key", "Person", "Person Key Used", "In Service Device Count",
            "PC Devices", "Monitor Devices", "Device Sources", "Investigation Status", "Comments"
        ]
    )
else:
    _summary_rows = []
    for _person_key, _group in _multi_device_details.groupby("Person Key"):
        _sources = sorted(_group["Source"].dropna().astype(str).unique()) if "Source" in _group.columns else []
        _pc_count = int(_group["Source"].astype(str).str.contains("Desktop|PC", case=False, na=False).sum()) if "Source" in _group.columns else 0
        _monitor_count = int(_group["Source"].astype(str).str.contains("Monitor", case=False, na=False).sum()) if "Source" in _group.columns else 0
        _summary_rows.append({
            "Person Key": _person_key,
            "Person": _group["Person"].iloc[0],
            "Person Key Used": _group["Person Key Used"].iloc[0],
            "In Service Device Count": len(_group),
            "PC Devices": _pc_count,
            "Monitor Devices": _monitor_count,
            "Device Sources": ", ".join(_sources),
            "Investigation Status": "To investigate",
            "Comments": "",
        })
    _multi_device_summary = pd.DataFrame(_summary_rows).sort_values(
        ["In Service Device Count", "Person"], ascending=[False, True]
    ).reset_index(drop=True)

_no_person_identifier = int((_multi_inservice["Person Key"].eq("")).sum())

md1, md2, md3 = st.columns(3)
md1.metric("People with >1 In-Service Device", f"{len(_multi_device_summary):,}")
md2.metric("Affected In-Service Records", f"{len(_multi_device_details):,}")
md3.metric("In-Service Records Missing Person ID", f"{_no_person_identifier:,}")

st.info(
    "How this is calculated: selected Contact Site(s) → combined PC + Monitor CMDB → "
    "Status = In Service → group by Employee ID (Username fallback) → flag people where device count > 1."
)

if _multi_device_summary.empty:
    st.success("No people with more than one identifiable In-Service CMDB device were found.")
else:
    _investigation_editor = st.data_editor(
        _multi_device_summary,
        use_container_width=True,
        hide_index=True,
        key="multi_inservice_investigation_editor",
        column_config={
            "Investigation Status": st.column_config.SelectboxColumn(
                "Investigation Status",
                options=["To investigate", "Valid exception", "Correction required", "Corrected"],
            )
        },
    )

    _selected_person = st.selectbox(
        "Select a person to view the In-Service CMDB records",
        _multi_device_summary["Person Key"].tolist(),
        format_func=lambda key: (
            f"{_multi_device_summary.loc[_multi_device_summary['Person Key'].eq(key), 'Person'].iloc[0]} "
            f"| {key} | "
            f"{int(_multi_device_summary.loc[_multi_device_summary['Person Key'].eq(key), 'In Service Device Count'].iloc[0])} devices"
        ),
        key="selected_multi_inservice_person",
    )
    _selected_details = _multi_device_details[_multi_device_details["Person Key"].eq(_selected_person)]
    st.dataframe(_selected_details, use_container_width=True, hide_index=True)

    # Export an investigation workbook for correction follow-up.
    _multi_buffer = io.BytesIO()
    with pd.ExcelWriter(_multi_buffer, engine="openpyxl") as _writer:
        _investigation_editor.to_excel(_writer, sheet_name="People to Investigate", index=False)
        _multi_device_details.to_excel(_writer, sheet_name="In Service Device Details", index=False)
        _unidentified = _multi_inservice[_multi_inservice["Person Key"].eq("")].copy()
        _unidentified.to_excel(_writer, sheet_name="Missing Person Identifier", index=False)
        for _ws in _writer.book.worksheets:
            _ws.freeze_panes = "A2"
            _ws.auto_filter.ref = _ws.dimensions
            for _cells in _ws.columns:
                _ws.column_dimensions[_cells[0].column_letter].width = min(
                    max(len(str(_cell.value or "")) for _cell in _cells) + 2, 42
                )
    _multi_buffer.seek(0)

    st.download_button(
        "📥 Download In-Service Multi-Device Investigation",
        data=_multi_buffer.getvalue(),
        file_name=f"{power_station}_InService_MultiDevice_Investigation.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="download_multi_inservice_investigation",
    )

st.warning(
    "The application flags records for investigation. Any CMDB correction should be verified "
    "against the actual user/device allocation and completed through the approved CMDB process."
)

'''

updated = text.replace(anchor, section + anchor, 1)
compile(updated, str(APP), 'exec')
APP.write_text(updated, encoding='utf-8')

print('SUCCESS: In-Service multi-device investigation installed.')
print(f'Backup created: {BACKUP.name}')
print('Rule: selected Contact Site(s) -> In Service -> Employee ID/Username -> more than 1 device.')
print('Next: python -m py_compile app.py')
