from pathlib import Path
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_multi_device_check_v2.py')
MARKER = '# MULTI_INSERVICE_DEVICE_CHECK_V2'

if not APP.exists():
    raise SystemExit('app.py not found. Put this installer next to app.py in KomatiAssetApp.')
text = APP.read_text(encoding='utf-8')
if MARKER in text:
    raise SystemExit('Multi-device V2 check is already installed.')
shutil.copy2(APP, BACKUP)

# Put the feature immediately before the visible Detailed reconciliation results heading.
anchor = 'st.subheader("Detailed reconciliation results")'
if anchor not in text:
    raise SystemExit('Could not find Detailed reconciliation results. No changes made.')

section = r'''# MULTI_INSERVICE_DEVICE_CHECK_V2
st.divider()
st.markdown("## 👤 People With More Than One In-Service Device")
st.caption(
    "Management investigation: selected Contact Site(s) → PC + Monitor CMDB → "
    "In Service only → Employee ID (Username fallback) → more than one device."
)

_multi_inservice = status_groups.get("In Service", pd.DataFrame()).copy()
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

if _employee_id_col:
    _person_key = _multi_inservice[_employee_id_col].fillna("").astype(str).str.strip().str.upper()
else:
    _person_key = pd.Series("", index=_multi_inservice.index, dtype=object)
if _username_col:
    _username_key = _multi_inservice[_username_col].fillna("").astype(str).str.strip().str.upper()
else:
    _username_key = pd.Series("", index=_multi_inservice.index, dtype=object)

_multi_inservice["Person Key"] = _person_key
_use_username = _multi_inservice["Person Key"].eq("") & _username_key.ne("")
_multi_inservice.loc[_use_username, "Person Key"] = _username_key[_use_username]
_multi_inservice["Person Key Used"] = "Employee ID"
_multi_inservice.loc[_use_username, "Person Key Used"] = "Username"
_multi_inservice.loc[_multi_inservice["Person Key"].eq(""), "Person Key Used"] = "No person identifier"

_first = _multi_inservice[_name_col].fillna("").astype(str).str.strip() if _name_col else pd.Series("", index=_multi_inservice.index)
_last = _multi_inservice[_surname_col].fillna("").astype(str).str.strip() if _surname_col else pd.Series("", index=_multi_inservice.index)
_multi_inservice["Person"] = (_first + " " + _last).str.strip()
_multi_inservice.loc[_multi_inservice["Person"].eq(""), "Person"] = _multi_inservice["Person Key"]

_identified = _multi_inservice[_multi_inservice["Person Key"].ne("")].copy()
_counts = _identified.groupby("Person Key").size()
_multi_keys = _counts[_counts > 1].index
_multi_details = _identified[_identified["Person Key"].isin(_multi_keys)].copy()

_summary_rows = []
for _key, _group in _multi_details.groupby("Person Key"):
    _sources = sorted(_group["Source"].dropna().astype(str).unique()) if "Source" in _group.columns else []
    _summary_rows.append({
        "Person": _group["Person"].iloc[0],
        "Employee / User Key": _key,
        "Key Used": _group["Person Key Used"].iloc[0],
        "In-Service Device Count": len(_group),
        "CMDB Sources": ", ".join(_sources),
        "Investigation Status": "To investigate",
        "Comments": "",
    })
_multi_summary = pd.DataFrame(_summary_rows)
if not _multi_summary.empty:
    _multi_summary = _multi_summary.sort_values(
        ["In-Service Device Count", "Person"], ascending=[False, True]
    ).reset_index(drop=True)

_missing_person = int(_multi_inservice["Person Key"].eq("").sum())
_mc1, _mc2, _mc3 = st.columns(3)
_mc1.metric("People With >1 In-Service Device", f"{len(_multi_summary):,}")
_mc2.metric("Affected In-Service Records", f"{len(_multi_details):,}")
_mc3.metric("Missing Person Identifier", f"{_missing_person:,}")

if _multi_summary.empty:
    st.success("No people with more than one identifiable In-Service CMDB device were found.")
else:
    _multi_editor = st.data_editor(
        _multi_summary,
        use_container_width=True,
        hide_index=True,
        key="multi_inservice_v2_editor",
        column_config={
            "Investigation Status": st.column_config.SelectboxColumn(
                "Investigation Status",
                options=["To investigate", "Valid exception", "Correction required", "Corrected"],
            )
        },
    )

    _selected_person_key = st.selectbox(
        "Select a person to view the CMDB devices",
        _multi_summary["Employee / User Key"].tolist(),
        format_func=lambda key: (
            f"{_multi_summary.loc[_multi_summary['Employee / User Key'].eq(key), 'Person'].iloc[0]} | "
            f"{key} | {_multi_summary.loc[_multi_summary['Employee / User Key'].eq(key), 'In-Service Device Count'].iloc[0]} devices"
        ),
        key="multi_inservice_v2_person",
    )
    st.markdown(
        "**Why flagged:** The same Employee ID / Username is linked to more than one "
        "CMDB device whose status is In Service. Review the actual allocation before correcting CMDB."
    )
    _selected_records = _multi_details[_multi_details["Person Key"].eq(_selected_person_key)]
    st.dataframe(_selected_records, use_container_width=True, hide_index=True)

    _buffer = io.BytesIO()
    with pd.ExcelWriter(_buffer, engine="openpyxl") as _writer:
        _multi_editor.to_excel(_writer, sheet_name="People to Investigate", index=False)
        _multi_details.to_excel(_writer, sheet_name="In Service Device Details", index=False)
        _multi_inservice[_multi_inservice["Person Key"].eq("")].to_excel(
            _writer, sheet_name="Missing Person Identifier", index=False
        )
    _buffer.seek(0)
    st.download_button(
        "📥 Download In-Service Multi-Device Investigation",
        data=_buffer.getvalue(),
        file_name=f"{power_station}_InService_MultiDevice_Investigation.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="download_multi_inservice_v2",
    )

st.warning(
    "This control flags records for investigation only. Verify the user/device allocation "
    "before making any CMDB correction."
)

'''

updated = text.replace(anchor, section + anchor, 1)
compile(updated, str(APP), 'exec')
APP.write_text(updated, encoding='utf-8')

print('SUCCESS: visible In-Service multi-device section installed.')
print(f'Backup created: {BACKUP.name}')
print('The section is inserted immediately before Detailed reconciliation results.')
print('Next: python -m py_compile app.py')
