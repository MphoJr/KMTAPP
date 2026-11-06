from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name("app.py")
BACKUP = Path(__file__).with_name("app_before_contact_site_selector.py")

if not APP.exists():
    raise SystemExit("app.py was not found. Put this installer next to app.py in KomatiAssetApp.")

text = APP.read_text(encoding="utf-8")
marker = "# CONTACT_SITE_SELECTOR_V1"
if marker in text:
    raise SystemExit("The Contact Site selector is already installed. No changes were made.")

# Add an optional exact-site override to the existing station filter function.
old_signature = "def filter_station(df, station):"
new_signature = "def filter_station(df, station, selected_contact_site=None):"
if old_signature not in text:
    raise SystemExit("Could not find filter_station() in app.py. No changes were made.")
text = text.replace(old_signature, new_signature, 1)

anchor = '''    if not site_col:
        return df.copy(), None, None
'''
replacement = '''    if not site_col:
        return df.copy(), None, None

    # CONTACT_SITE_SELECTOR_V1
    # If the user selects an actual Contact Site from the uploaded CMDB,
    # use that exact value instead of relying on a hard-coded station map.
    if selected_contact_site:
        cleaned_site = df[site_col].fillna("").astype(str).str.strip().str.upper()
        mask = cleaned_site.eq(str(selected_contact_site).strip().upper())
        return df.loc[mask].copy(), site_col, selected_contact_site
'''
if anchor not in text:
    raise SystemExit("Could not find the filter_station() insertion point. No changes were made.")
text = text.replace(anchor, replacement, 1)

# Allow the pre-comparison function to receive the selected exact Contact Site.
old_pre_sig = "def build_precomparison_status_summary(frame, source_name, station):"
new_pre_sig = "def build_precomparison_status_summary(frame, source_name, station, selected_contact_site=None):"
if old_pre_sig not in text:
    raise SystemExit("Could not find build_precomparison_status_summary(). No changes were made.")
text = text.replace(old_pre_sig, new_pre_sig, 1)
text = text.replace(
    "station_rows, site_column, site_filter = filter_station(frame, station)",
    "station_rows, site_column, site_filter = filter_station(frame, station, selected_contact_site)",
    1,
)

# Insert actual Contact Site selection after the three worksheets have been read.
worksheet_marker = 'progress_bar.progress(55, text="Worksheets loaded. Preparing asset data...")'
selector_code = '''progress_bar.progress(55, text="Worksheets loaded. Preparing asset data...")

# CONTACT SITE SELECTION FROM THE UPLOADED CMDB FILES
st.subheader("3. Confirm CMDB Contact Site")
st.caption(
    "Select the exact Contact Site that must be analysed. The app uses this value "
    "for both the Desktop and Monitor CMDB status summary and station comparison."
)

def contact_site_values(dataframe):
    column = find_column(
        dataframe.columns,
        ["Contact Site", "Site", "Location", "Physical Location", "Power Station"],
    )
    if not column:
        return None, []
    values = sorted(
        value
        for value in dataframe[column].fillna("").astype(str).str.strip().unique()
        if value
    )
    return column, values

_desktop_site_column, _desktop_sites = contact_site_values(desktop_df)
_monitor_site_column, _monitor_sites = contact_site_values(monitor_df)
_common_sites = sorted(set(_desktop_sites).intersection(_monitor_sites))
_all_sites = _common_sites or sorted(set(_desktop_sites).union(_monitor_sites))

if not _all_sites:
    st.error("No Contact Site values were detected in the uploaded Desktop or Monitor CMDB files.")
    st.stop()

_station_hint = power_station.upper()
_default_index = 0
for _index, _site in enumerate(_all_sites):
    if _station_hint in _site.upper():
        _default_index = _index
        break

selected_contact_site = st.selectbox(
    "CMDB Contact Site",
    _all_sites,
    index=_default_index,
    key="selected_contact_site",
)

st.info(f"CMDB records will be filtered using the exact Contact Site: {selected_contact_site}")
'''
if worksheet_marker not in text:
    raise SystemExit("Could not find the worksheet-loading insertion point. No changes were made.")
text = text.replace(worksheet_marker, selector_code, 1)

# Existing section numbering shifts by one after the new Contact Site section.
text = text.replace('st.subheader("3. Select local assets in scope")', 'st.subheader("4. Select local assets in scope")', 1)
text = text.replace('st.subheader("4. Confirm identifier columns")', 'st.subheader("5. Confirm identifier columns")', 1)

# Pass the selected site into both pre-reconciliation calls.
text = text.replace(
'''        "Desktop CMDB",
        power_station,
    )''',
'''        "Desktop CMDB",
        power_station,
        selected_contact_site,
    )''',
1,
)
text = text.replace(
'''        "Monitor CMDB",
        power_station,
    )''',
'''        "Monitor CMDB",
        power_station,
        selected_contact_site,
    )''',
1,
)

# Include Contact Site in the comparison signature so a changed site resets results.
sig_anchor = '''        + str(local_sheet)
        + str(scope_column)'''
sig_replacement = '''        + str(local_sheet)
        + str(selected_contact_site)
        + str(scope_column)'''
if sig_anchor not in text:
    raise SystemExit("Could not find the comparison-signature insertion point. No changes were made.")
text = text.replace(sig_anchor, sig_replacement, 1)

# Make the downstream CMDB station filter use the same selected Contact Site.
text = text.replace(
    "station_cmdb, site_column, station_filter = filter_station(cmdb_results, power_station)",
    "station_cmdb, site_column, station_filter = filter_station(cmdb_results, power_station, selected_contact_site)",
    1,
)

# Store the selected Contact Site in audit and report summary.
audit_anchor = '''    "Power Station": power_station,
    "Reconciliation Date":'''
audit_replacement = '''    "Power Station": power_station,
    "Contact Site": selected_contact_site,
    "Reconciliation Date":'''
if audit_anchor in text:
    text = text.replace(audit_anchor, audit_replacement, 1)

# Correct the later Loan dashboard bug if it still exists.
text = text.replace(
    '["loan", "onloan", "loaned"]',
    '["loan", "loanunit", "onloan", "loaned"]',
)

compile(text, str(APP), "exec")
shutil.copy2(APP, BACKUP)
APP.write_text(text, encoding="utf-8")

print("Contact Site selector installed successfully.")
print(f"Backup created: {BACKUP.name}")
print("The app now uses the exact Contact Site selected from the uploaded CMDB files.")
print("The selected Contact Site is also included in the reconciliation signature and audit record.")
