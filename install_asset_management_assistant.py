from pathlib import Path
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_asset_management_assistant.py')
MARKER = '# ASSET_MANAGEMENT_ASSISTANT_V1'

if not APP.exists():
    raise SystemExit('app.py not found. Put this installer in KomatiAssetApp next to app.py.')

text = APP.read_text(encoding='utf-8')
if MARKER in text:
    raise SystemExit('Asset Management Assistant is already installed.')

shutil.copy2(APP, BACKUP)
anchor = '# -----------------------------------------------------------------------------\n# DETAILED RESULTS / EXPORT'
if anchor not in text:
    raise SystemExit('Could not find the Detailed Results section. No changes were made.')

section = r'''# ASSET_MANAGEMENT_ASSISTANT_V1
st.divider()
st.markdown("## 📦 Asset Management Assistant")
st.caption(
    f"Asset-management view for {power_station} using the selected CMDB Contact Site "
    f"and the current Local Asset Register. System-calculated fields are shown first; "
    f"reallocation, scrapping, contingency and issuing-plan decisions remain user-controlled."
)

# Find common descriptive fields without requiring fixed column names.
_am_model_col = find_column(
    local_results.columns,
    ["Model", "Asset Model", "Equipment Model", "Product Model", "Description"],
)
_am_mfr_col = find_column(
    local_results.columns,
    ["Manufacturer", "Make", "Vendor"],
)
_am_user_col = find_column(
    local_results.columns,
    ["Username", "User Name", "Assigned User", "Employee ID", "User Unique Number", "Personnel Number"],
)

asset_management_local = local_results.copy()

if _am_model_col:
    asset_management_local["Asset Model"] = (
        asset_management_local[_am_model_col]
        .fillna("Unknown Model")
        .astype(str)
        .str.strip()
        .replace({"": "Unknown Model"})
    )
else:
    asset_management_local["Asset Model"] = asset_management_local.get(
        "Asset Type", pd.Series("Unknown Model", index=asset_management_local.index)
    ).fillna("Unknown Model").astype(str)

if _am_mfr_col:
    asset_management_local["Manufacturer / Make"] = (
        asset_management_local[_am_mfr_col].fillna("").astype(str).str.strip()
    )
else:
    asset_management_local["Manufacturer / Make"] = ""

if _am_user_col:
    _issued_key = (
        asset_management_local[_am_user_col].fillna("").astype(str).str.strip()
    )
    asset_management_local["Local Position"] = _issued_key.ne("").map(
        {True: "Issued", False: "In Storeroom"}
    )
else:
    asset_management_local["Local Position"] = "Review Required"

# Model-level local asset summary.
_am_rows = []
for _model, _group in asset_management_local.groupby("Asset Model", dropna=False):
    _total = len(_group)
    _issued = int((_group["Local Position"] == "Issued").sum())
    _store = int((_group["Local Position"] == "In Storeroom").sum())
    _found = int(_group["Matched"].fillna(False).astype(bool).sum()) if "Matched" in _group else 0
    _missing = _total - _found
    _cmdb_pct = (_found / _total * 100) if _total else 0
    _am_rows.append({
        "Asset Model": _model,
        "Local Total": _total,
        "Number Issued": _issued,
        "Number in Storeroom": _store,
        "Found on CMDB": _found,
        "Missing on CMDB": _missing,
        "CMDB %": round(_cmdb_pct, 1),
    })
asset_management_summary = pd.DataFrame(_am_rows)
if not asset_management_summary.empty:
    asset_management_summary = asset_management_summary.sort_values(
        ["Local Total", "Asset Model"], ascending=[False, True]
    ).reset_index(drop=True)

# CMDB status groups already honour the selected Contact Site and combine PC + Monitor.
_am_preprod = status_groups.get("Preproduction", pd.DataFrame()).copy()
_am_in_service = status_groups.get("In Service", pd.DataFrame()).copy()
_am_in_store = status_groups.get("In Store", pd.DataFrame()).copy()
_am_retired = status_groups.get("Retired", pd.DataFrame()).copy()

am1, am2, am3, am4 = st.columns(4)
am1.metric("Local Assets", f"{len(asset_management_local):,}")
am2.metric("Preproduction CMDB", f"{len(_am_preprod):,}")
am3.metric("In Store CMDB", f"{len(_am_in_store):,}")
am4.metric("Old / Retired CMDB", f"{len(_am_retired):,}")

st.markdown("### 🟡 New / Preproduction Assets")
st.caption(
    "Preproduction is taken from CMDB for the selected Contact Site. "
    "Use the detail table to identify the exact PC or Monitor records and models."
)
st.dataframe(_am_preprod, use_container_width=True, hide_index=True)

st.markdown("### 📦 Local Asset Position by Model")
if _am_user_col:
    st.caption(
        f"Issued / Storeroom is inferred from the local user field `{_am_user_col}`: "
        "a populated user is treated as Issued; a blank user is treated as In Storeroom. "
        "Review this rule before relying on it where local business practice differs."
    )
else:
    st.warning(
        "No local user-allocation column was detected, so Issued / Storeroom cannot be inferred automatically."
    )
st.dataframe(asset_management_summary, use_container_width=True, hide_index=True)

with st.expander("View assets behind the model totals"):
    _model_options = asset_management_summary["Asset Model"].tolist() if not asset_management_summary.empty else []
    if _model_options:
        _selected_model = st.selectbox(
            "Asset model",
            _model_options,
            key="asset_management_model_filter",
        )
        st.dataframe(
            asset_management_local[asset_management_local["Asset Model"].eq(_selected_model)],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No model-level Local Asset information is available.")

st.markdown("### ♻️ Old Asset Management")
st.caption(
    "Retired CMDB assets are presented as candidates for review. The application does not "
    "automatically decide reallocation or scrapping."
)
st.dataframe(_am_retired, use_container_width=True, hide_index=True)

# Editable governance decisions for Mpume / station asset management.
if not asset_management_summary.empty:
    _decision_editor = asset_management_summary[["Asset Model"]].copy()
    _decision_editor["Contingency Assets"] = 0
    _decision_editor["Asset Issuing Plan"] = ""
    _decision_editor["Assets for Re-allocation"] = 0
    _decision_editor["Assets for Scrapping"] = 0
    _decision_editor["Scrapping Progress %"] = 0
    _decision_editor["Comments"] = ""

    st.markdown("### ✍️ Asset Management Decisions")
    st.caption(
        "These fields require asset-management confirmation. They are intentionally not decided automatically."
    )
    asset_management_decisions = st.data_editor(
        _decision_editor,
        use_container_width=True,
        hide_index=True,
        key="asset_management_decisions",
    )
else:
    asset_management_decisions = pd.DataFrame()

# Build an Excel workbook similar in purpose to Mpume's template.
def build_asset_management_workbook():
    _buffer = io.BytesIO()
    with pd.ExcelWriter(_buffer, engine="openpyxl") as _writer:
        asset_management_summary.to_excel(
            _writer, sheet_name="Asset Summary", index=False
        )
        _am_preprod.to_excel(
            _writer, sheet_name="Preproduction Assets", index=False
        )
        asset_management_local[
            asset_management_local["Local Position"].eq("Issued")
        ].to_excel(_writer, sheet_name="Issued Assets", index=False)
        asset_management_local[
            asset_management_local["Local Position"].eq("In Storeroom")
        ].to_excel(_writer, sheet_name="Storeroom Assets", index=False)
        asset_management_local[
            ~asset_management_local["Matched"].fillna(False).astype(bool)
        ].to_excel(_writer, sheet_name="Missing on CMDB", index=False)
        _am_retired.to_excel(_writer, sheet_name="Old Retired Assets", index=False)
        asset_management_decisions.to_excel(
            _writer, sheet_name="Management Decisions", index=False
        )

        _summary_info = pd.DataFrame(
            {
                "Measure": [
                    "Power Station",
                    "Contact Site",
                    "Local Assets",
                    "PC + Monitor Preproduction",
                    "PC + Monitor In Service",
                    "PC + Monitor In Store",
                    "PC + Monitor Retired",
                ],
                "Value": [
                    power_station,
                    selected_contact_site,
                    len(asset_management_local),
                    len(_am_preprod),
                    len(_am_in_service),
                    len(_am_in_store),
                    len(_am_retired),
                ],
            }
        )
        _summary_info.to_excel(
            _writer, sheet_name="Station Summary", index=False
        )

        for _ws in _writer.book.worksheets:
            _ws.freeze_panes = "A2"
            _ws.auto_filter.ref = _ws.dimensions
            for _cells in _ws.columns:
                _width = min(
                    max(len(str(_cell.value or "")) for _cell in _cells) + 2,
                    45,
                )
                _ws.column_dimensions[_cells[0].column_letter].width = _width

    _buffer.seek(0)
    return _buffer.getvalue()

_asset_management_report = build_asset_management_workbook()
st.download_button(
    "📥 Download Asset Management Template",
    data=_asset_management_report,
    file_name=f"{power_station}_Asset_Management_Template.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
    key="download_asset_management_template",
)

st.info(
    "System-calculated information supports the asset-management process; "
    "Contingency, issuing, reallocation and scrapping decisions remain subject to review and approval."
)

'''

updated = text.replace(anchor, section + anchor, 1)
compile(updated, str(APP), 'exec')
APP.write_text(updated, encoding='utf-8')

print('SUCCESS: Asset Management Assistant installed.')
print(f'Backup created: {BACKUP.name}')
print('Includes Preproduction, model-level local totals, issued/storeroom inference, CMDB %, old/retired assets, editable decisions, and Excel download.')
print('Next: python -m py_compile app.py')
