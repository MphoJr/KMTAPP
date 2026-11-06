from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name("app.py")
BACKUP = Path(__file__).with_name("app_before_departmental_feedback.py")
MARKER = "# OU_IM_DEPARTMENTAL_FEEDBACK_V1"

if not APP.exists():
    raise SystemExit("app.py was not found. Put this file next to app.py in KomatiAssetApp.")

text = APP.read_text(encoding="utf-8")
if MARKER in text:
    raise SystemExit("The departmental-feedback upgrade is already installed. No changes were made.")

shutil.copy2(APP, BACKUP)

# -------------------------------------------------------------------------
# 1. DUPLICATE REPORTING: one row per repeated identifier, not every row.
# -------------------------------------------------------------------------
duplicate_function = r'''

# OU_IM_DEPARTMENTAL_FEEDBACK_V1
def duplicate_identifier_summary(df, dataset_name):
    """Return one investigation row per repeated identifier.

    This avoids presenting every duplicated source row as a separate duplicate
    asset. The output explains the identifier type, value and number of source
    records that share it.
    """
    checks = [
        ("Primary Key", "Primary identifier"),
        ("Serial Key", "Serial Number"),
        ("Asset Key", "Asset Number"),
    ]
    issues = []

    for column, label in checks:
        if column not in df.columns:
            continue

        values = df[column].fillna("").astype(str).str.strip()
        counts = values[values.ne("")].value_counts()

        for identifier_value, record_count in counts[counts > 1].items():
            records = df.loc[values.eq(identifier_value)].copy()

            source_values = []
            if "Source" in records.columns:
                source_values = sorted(
                    value for value in records["Source"].dropna().astype(str).unique()
                    if value
                )

            status_values = []
            for status_column in [
                "CMDB Device Status",
                "Device Status",
                "Status",
                "Status Group",
            ]:
                if status_column in records.columns:
                    status_values.extend(
                        value
                        for value in records[status_column].dropna().astype(str).unique()
                        if value
                    )

            issues.append(
                {
                    "Dataset": dataset_name,
                    "Identifier Type": label,
                    "Identifier Value": identifier_value,
                    "Source Record Count": int(record_count),
                    "Sources": ", ".join(sorted(set(source_values))),
                    "Statuses": ", ".join(sorted(set(status_values))),
                    "Investigation Note": (
                        "Repeated identifier detected. Confirm whether these are "
                        "duplicate records, shared identifiers, or valid historical records."
                    ),
                }
            )

    columns = [
        "Dataset",
        "Identifier Type",
        "Identifier Value",
        "Source Record Count",
        "Sources",
        "Statuses",
        "Investigation Note",
    ]
    return pd.DataFrame(issues, columns=columns)

'''

# Insert before filter_station, a stable function boundary in this app.
function_anchor = "\ndef filter_station("
if function_anchor not in text:
    raise SystemExit("Could not find filter_station() in app.py. No changes were made.")
text = text.replace(function_anchor, duplicate_function + function_anchor, 1)

# Replace inflated row-level duplicate outputs with identifier-level summaries.
old_cmdb = "duplicate_cmdb = duplicate_rows(cmdb)"
old_local = "duplicate_local = duplicate_rows(local)"
if old_cmdb not in text or old_local not in text:
    raise SystemExit("Could not find duplicate calculations in app.py. No changes were made.")
text = text.replace(
    old_cmdb,
    'duplicate_cmdb = duplicate_identifier_summary(cmdb, "CMDB")',
    1,
)
text = text.replace(
    old_local,
    'duplicate_local = duplicate_identifier_summary(local, "Local Register")',
    1,
)

# Rename duplicate labels without changing calculation variable names.
label_replacements = {
    '"CMDB duplicate rows"': '"CMDB duplicate identifiers"',
    '"Local duplicate rows"': '"Local duplicate identifiers"',
    '"CMDB Duplicates"': '"CMDB Duplicate Identifiers"',
    '"Local Duplicates"': '"Local Duplicate Identifiers"',
    '"CMDB duplicates"': '"CMDB duplicate identifiers"',
    '"Local duplicates"': '"Local duplicate identifiers"',
}
for old, new in label_replacements.items():
    text = text.replace(old, new)

# Add an explanation above detailed-results tabs.
detailed_anchor = 'st.subheader("Detailed reconciliation results")'
detailed_replacement = '''st.subheader("Detailed reconciliation results")
st.info(
    "Duplicate tabs now show one row per repeated identifier, not every source row. "
    "A repeated identifier is an investigation item and does not automatically mean "
    "that multiple physical devices exist or that a record must be deleted."
)'''
if detailed_anchor in text:
    text = text.replace(detailed_anchor, detailed_replacement, 1)

# -------------------------------------------------------------------------
# 2. ACTIVE-ASSET VIEW: In Service + Preproduction only.
# -------------------------------------------------------------------------
active_section = r'''

# Active Asset Reconciliation requested during the OU IM Departmental Meeting.
st.markdown("## 🟢 Active Asset Reconciliation")
st.caption(
    "This view is limited to CMDB assets in **In Service** and "
    "**Preproduction** status for the selected Contact Site. Retired, In Store "
    "and Loan assets are excluded from this active-asset rate."
)

_active_frames = [
    status_groups.get("In Service", pd.DataFrame()),
    status_groups.get("Preproduction", pd.DataFrame()),
]
_active_frames = [frame for frame in _active_frames if not frame.empty]

if _active_frames:
    active_cmdb_assets = pd.concat(_active_frames, ignore_index=True, sort=False)
else:
    active_cmdb_assets = pd.DataFrame()

active_cmdb_total = len(active_cmdb_assets)
active_found_locally = (
    int(active_cmdb_assets["Matched"].fillna(False).astype(bool).sum())
    if active_cmdb_total and "Matched" in active_cmdb_assets.columns
    else 0
)
active_missing_locally = active_cmdb_total - active_found_locally
active_match_rate = (
    active_found_locally / active_cmdb_total * 100
    if active_cmdb_total
    else 0
)

active1, active2, active3, active4 = st.columns(4)
active1.metric("Active CMDB Assets", f"{active_cmdb_total:,}")
active2.metric("Found in Local Register", f"{active_found_locally:,}")
active3.metric("Not Found Locally", f"{active_missing_locally:,}")
active4.metric("Active Asset Match Rate", f"{active_match_rate:.1f}%")

with st.expander("View active CMDB asset details"):
    st.dataframe(active_cmdb_assets, use_container_width=True, hide_index=True)

'''

# Insert before the existing executive dashboard, after status_groups exists.
dashboard_anchor = '# -----------------------------------------------------------------------------\n# EXECUTIVE DASHBOARD'
if dashboard_anchor not in text:
    raise SystemExit("Could not find the Executive Dashboard section. No changes were made.")
text = text.replace(dashboard_anchor, active_section + dashboard_anchor, 1)

# -------------------------------------------------------------------------
# 3. TERMINOLOGY: make clear that PC includes desktops and laptops.
# -------------------------------------------------------------------------
visible_replacements = {
    'st.markdown("#### 🖥️ Desktop")': 'st.markdown("#### 🖥️ PC CMDB")',
    '"Desktop CMDB", type=': '"PC CMDB (Desktops & Laptops)", type=',
    '"Desktop worksheet"': '"PC CMDB worksheet"',
    '"Desktop Hostname"': '"PC Hostname"',
    '"Desktop CMDB Status"': '"PC CMDB Status"',
    '"Desktop records for selected station"': '"PC records for selected station"',
    '"View exact Desktop CMDB status values"': '"View exact PC CMDB status values"',
    '"Desktop status to view"': '"PC status to view"',
}
for old, new in visible_replacements.items():
    text = text.replace(old, new)

# Add a clear terminology note near the upload section.
upload_caption_anchor = 'st.markdown("### 📁 Asset Data Sources")'
upload_caption_replacement = '''st.markdown("### 📁 Asset Data Sources")
st.caption(
    "**PC CMDB** includes both desktop computers and laptops. "
    "Monitor CMDB is processed separately."
)'''
if upload_caption_anchor in text:
    text = text.replace(upload_caption_anchor, upload_caption_replacement, 1)

# Syntax validation before writing.
compile(text, str(APP), "exec")
APP.write_text(text, encoding="utf-8")

print("SUCCESS: OU IM departmental feedback applied.")
print(f"Backup created: {BACKUP.name}")
print("1. Duplicate reporting now counts repeated identifiers, not duplicate rows.")
print("2. Active Asset Match Rate added for In Service + Preproduction.")
print("3. User-facing Desktop terminology clarified as PC CMDB.")
print("Next: python -m py_compile app.py")
