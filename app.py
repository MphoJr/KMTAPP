import hashlib
import io
import os
import re
from datetime import datetime
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Komati App is running!"

if __name__ == "__main__":
    app.run()

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="CMDB Reconciliation & Asset Governance Tool",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
<style>
.stApp {background-color:#F0F4F8;}
[data-testid="stSidebar"] {background-color:#001C38;}
[data-testid="stSidebar"] * {color:#E2E8F0 !important;}
h1 {color:#00204A;font-weight:800;border-bottom:4px solid #FFB800;padding-bottom:8px;}
h2,h3 {color:#003366;font-weight:700;}
[data-testid="stMetric"] {background:#FFF;border-left:5px solid #005696;border-radius:8px;padding:16px;box-shadow:0 3px 10px rgba(0,32,74,.08);}
[data-testid="stMetricValue"] {color:#005696;font-weight:800;}
[data-testid="stFileUploader"] {background:#FFF;border:2px dashed #005696;border-radius:10px;padding:14px;}
.stButton > button,.stDownloadButton > button {background:#FFB800;color:#001C38;border:none;border-radius:6px;font-weight:800;}
</style>
""",
    unsafe_allow_html=True,
)

MAX_FILE_SIZE = 50 * 1024 * 1024
NOT_AVAILABLE = "-- Not available --"
NO_FILTER = "-- Include all local assets --"
HISTORY_FILE = "reconciliation_history.csv"


def clean_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def normalize(series):
    return (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\s+", "", regex=True)
        .replace({"NAN": "", "NONE": "", "N/A": "", "NA": ""})
    )


def fingerprint(uploaded_file):
    uploaded_file.seek(0)
    value = hashlib.sha256(uploaded_file.read()).hexdigest()
    uploaded_file.seek(0)
    return value


def validate_uploads(files):
    errors = []
    for role, uploaded_file in files.items():
        if not uploaded_file.name.lower().endswith((".xlsx", ".xls")):
            errors.append(f"{role}: only Excel files are accepted.")
        if uploaded_file.size == 0:
            errors.append(f"{role}: file is empty.")
        if uploaded_file.size > MAX_FILE_SIZE:
            errors.append(f"{role}: file is larger than 50 MB.")

    hashes = {role: fingerprint(file) for role, file in files.items()}
    roles = list(hashes)
    for i, first in enumerate(roles):
        for second in roles[i + 1 :]:
            if hashes[first] == hashes[second]:
                errors.append(
                    f"The same workbook was uploaded for {first} and {second}."
                )
    return errors


def sheet_names(uploaded_file):
    uploaded_file.seek(0)
    return pd.ExcelFile(uploaded_file).sheet_names


@st.cache_data(show_spinner="Loading asset data...")
def read_sheet(uploaded_file, sheet):
    uploaded_file.seek(0)
    return pd.read_excel(uploaded_file, sheet_name=sheet, dtype=object)


def find_column(columns, candidates):
    exact = {clean_name(column): column for column in columns}
    for candidate in candidates:
        key = clean_name(candidate)
        if key in exact:
            return exact[key]
    for column in columns:
        for candidate in candidates:
            if clean_name(candidate) in clean_name(column):
                return column
    return None


def workbook_has_any_column(uploaded_file, candidates):
    """Lightweight role check across all sheets and first 15 rows."""
    try:
        uploaded_file.seek(0)
        book = pd.ExcelFile(uploaded_file)
        expected = {clean_name(c) for c in candidates}
        for sheet in book.sheet_names:
            for header_row in range(0, 15):
                uploaded_file.seek(0)
                try:
                    probe = pd.read_excel(
                        uploaded_file,
                        sheet_name=sheet,
                        header=header_row,
                        nrows=3,
                    )
                except Exception:
                    continue
                columns = {clean_name(c) for c in probe.columns}
                if columns & expected:
                    uploaded_file.seek(0)
                    return True
    except Exception:
        pass
    uploaded_file.seek(0)
    return False


def selector(label, df, candidates, key):
    options = [NOT_AVAILABLE] + list(df.columns)
    default = find_column(df.columns, candidates)
    index = options.index(default) if default in options else 0
    return st.selectbox(label, options, index=index, key=key)


def identifier(df, column):
    if column == NOT_AVAILABLE:
        return pd.Series("", index=df.index, dtype=object)
    return normalize(df[column])


def prepare(df, primary, serial, asset, source, asset_type, primary_label):
    out = df.copy()
    out["Primary Key"] = identifier(out, primary)
    out["Serial Key"] = identifier(out, serial)
    out["Asset Key"] = identifier(out, asset)
    out["Comparison Key"] = out["Primary Key"]
    out["Key Used"] = primary_label

    mask = out["Comparison Key"].eq("") & out["Serial Key"].ne("")
    out.loc[mask, "Comparison Key"] = out.loc[mask, "Serial Key"]
    out.loc[mask, "Key Used"] = "Serial Number"

    mask = out["Comparison Key"].eq("") & out["Asset Key"].ne("")
    out.loc[mask, "Comparison Key"] = out.loc[mask, "Asset Key"]
    out.loc[mask, "Key Used"] = "Asset Number"

    out.loc[out["Comparison Key"].eq(""), "Key Used"] = "No identifier"
    out["Source"] = source
    out["Asset Type"] = asset_type
    return out


def id_sets(df):
    return {
        "Primary": set(df.loc[df["Primary Key"].ne(""), "Primary Key"]),
        "Serial": set(df.loc[df["Serial Key"].ne(""), "Serial Key"]),
        "Asset": set(df.loc[df["Asset Key"].ne(""), "Asset Key"]),
    }


def match_rows(df, sets):
    out = df.copy()
    out["Matched"] = False
    out["Matched By"] = ""
    out["Matched Value"] = ""
    checks = [
        ("Primary Key", "Primary", "Hostname or Asset Tag / UID"),
        ("Serial Key", "Serial", "Serial Number"),
        ("Asset Key", "Asset", "Asset Number"),
    ]
    for column, set_name, label in checks:
        mask = (~out["Matched"]) & out[column].ne("") & out[column].isin(sets[set_name])
        out.loc[mask, "Matched"] = True
        out.loc[mask, "Matched By"] = label
        out.loc[mask, "Matched Value"] = out.loc[mask, column]
    return out


def duplicate_rows(df):
    mask = pd.Series(False, index=df.index)
    for column in ["Primary Key", "Serial Key", "Asset Key"]:
        mask |= df[column].ne("") & df.duplicated(column, keep=False)
    return df.loc[mask].copy()



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



# COLORED_DUPLICATE_INVESTIGATION_V1
def classify_duplicate_evidence(records, identifier_type):
    """Classify why a repeated identifier requires investigation."""
    if records.empty:
        return "Review", "⚪", "No supporting records were available."

    sources = set(records.get("Source", pd.Series(dtype=str)).dropna().astype(str))

    comparison_columns = [
        "Primary Key",
        "Serial Key",
        "Asset Key",
        "CMDB Device Status",
        "Device Status",
        "Status",
        "Asset Type",
    ]
    conflict_columns = []
    for column in comparison_columns:
        if column in records.columns:
            values = {
                value
                for value in records[column].fillna("").astype(str).str.strip()
                if value
            }
            if len(values) > 1:
                conflict_columns.append(column)

    if len(sources) > 1:
        reason = (
            f"The {identifier_type} appears across multiple CMDB sources: "
            f"{', '.join(sorted(sources))}."
        )
        return "Cross-source", "🔵", reason

    if conflict_columns:
        reason = (
            f"The {identifier_type} is repeated and has conflicting values in: "
            f"{', '.join(conflict_columns)}."
        )
        return "Conflict", "🔴", reason

    return (
        "Repeated identical",
        "🟠",
        f"The {identifier_type} is repeated, while the compared supporting fields are largely identical.",
    )


def build_duplicate_investigation(df, dataset_name):
    """Create one coloured investigation row per repeated identifier."""
    checks = [
        ("Primary Key", "Primary identifier"),
        ("Serial Key", "Serial Number"),
        ("Asset Key", "Asset Number"),
    ]
    issues = []
    evidence = {}

    for column, label in checks:
        if column not in df.columns:
            continue

        values = df[column].fillna("").astype(str).str.strip()
        counts = values[values.ne("")].value_counts()

        for identifier_value, source_count in counts[counts > 1].items():
            records = df.loc[values.eq(identifier_value)].copy()
            classification, indicator, reason = classify_duplicate_evidence(records, label)
            issue_key = f"{dataset_name}|{label}|{identifier_value}"

            sources = []
            if "Source" in records.columns:
                sources = sorted(
                    value for value in records["Source"].dropna().astype(str).unique()
                    if value
                )

            issues.append(
                {
                    "Risk": f"{indicator} {classification}",
                    "Identifier Type": label,
                    "Identifier Value": identifier_value,
                    "Affected Records": int(source_count),
                    "Sources": ", ".join(sources),
                    "Why Flagged": reason,
                    "Issue Key": issue_key,
                }
            )
            evidence[issue_key] = records

    columns = [
        "Risk",
        "Identifier Type",
        "Identifier Value",
        "Affected Records",
        "Sources",
        "Why Flagged",
        "Issue Key",
    ]
    return pd.DataFrame(issues, columns=columns), evidence


def duplicate_table_style(row):
    risk = str(row.get("Risk", ""))
    if "🔴" in risk:
        color = "background-color: #FDE2E1; color: #8B0000;"
    elif "🟠" in risk:
        color = "background-color: #FFF1CC; color: #7A4E00;"
    elif "🔵" in risk:
        color = "background-color: #DCEEFF; color: #003B71;"
    elif "🟢" in risk:
        color = "background-color: #DFF3E4; color: #145A32;"
    else:
        color = "background-color: #ECEFF1; color: #37474F;"
    return [color] * len(row)


def filter_station(df, station, selected_contact_site=None):
    """Filter CMDB to one or more exact Contact Sites selected by the user."""
    site_col = find_column(
        df.columns,
        ["Contact Site", "Site", "Location", "Physical Location", "Power Station"],
    )
    if not site_col:
        return df.copy(), None, selected_contact_site or station

    cleaned_site = df[site_col].fillna("").astype(str).str.strip().str.upper()

    if selected_contact_site:
        if isinstance(selected_contact_site, (list, tuple, set)):
            selected_sites = [str(value).strip() for value in selected_contact_site if str(value).strip()]
        else:
            selected_sites = [str(selected_contact_site).strip()]

        selected_upper = {value.upper() for value in selected_sites}
        mask = cleaned_site.isin(selected_upper)
        return df.loc[mask].copy(), site_col, selected_sites

    # Backward-compatible fallback when no user-selected Contact Site is available.
    mask = cleaned_site.str.contains(str(station).upper(), na=False, regex=False)
    return df.loc[mask].copy(), site_col, [station]


def build_precomparison_status_summary(frame, source_name, station, selected_contact_site=None):
    """Filter by selected station Contact Site only, then count all statuses."""
    # Desktop uses Device Status. Monitor uses Status.
    status_candidates = (
        ["Device Status"]
        if source_name == "Desktop CMDB"
        else ["Status"]
    )

    station_rows, site_column, site_filter = filter_station(frame, station, selected_contact_site)
    status_column = find_column(station_rows.columns, status_candidates)

    labels = [
        "In Service",
        "In Store",
        "Retired",
        "Loan",
        "Preproduction",
        "Other / Unknown",
    ]
    counts = {label: 0 for label in labels}
    empty_raw = pd.DataFrame(
        columns=["Raw CMDB Status", "Mapped Status", "Count"]
    )

    if not status_column:
        return (
            station_rows,
            status_column,
            counts,
            empty_raw,
            site_column,
            site_filter,
        )

    result = station_rows.copy()
    result["Raw CMDB Status"] = (
        result[status_column]
        .fillna("(Blank)")
        .astype(str)
        .str.strip()
        .replace({"": "(Blank)"})
    )

    def map_vba_status(value):
        # Same classification and order as the supplied Excel VBA macro.
        status_text = str(value).strip().upper()
        if "SER" in status_text:
            return "In Service"
        elif "STO" in status_text:
            return "In Store"
        elif "RET" in status_text:
            return "Retired"
        elif "LOAN" in status_text:
            return "Loan"
        elif "PRE" in status_text or "PROD" in status_text:
            return "Preproduction"
        return "Other / Unknown"

    result["Status Group"] = result["Raw CMDB Status"].map(map_vba_status)
    result["CMDB Source"] = source_name

    for label in labels:
        counts[label] = int((result["Status Group"] == label).sum())

    raw_counts = (
        result.groupby(["Raw CMDB Status", "Status Group"], dropna=False)
        .size()
        .reset_index(name="Count")
        .rename(columns={"Status Group": "Mapped Status"})
        .sort_values(["Mapped Status", "Raw CMDB Status"])
        .reset_index(drop=True)
    )

    return (
        result,
        status_column,
        counts,
        raw_counts,
        site_column,
        site_filter,
    )



def build_status_results(desktop, monitor, local, station, selected_contact_sites=None):
    """Build combined Desktop + Monitor status groups and compare them with Local."""
    frames = []
    detected = []

    # Use the correct status column for each data source.
    for frame, source, candidates in [
        (desktop, "Desktop CMDB", ["Device Status"]),
        (monitor, "Monitor CMDB", ["Status"]),
    ]:
        column = find_column(frame.columns, candidates)
        if column:
            item = frame.copy()
            item["CMDB Device Status"] = (
                item[column].fillna("").astype(str).str.strip()
            )
            item["Status Source Column"] = column
            item["CMDB Source"] = source
            frames.append(item)
            detected.append(f"{source}: {column}")

    empty = desktop.iloc[0:0].copy()
    if not frames:
        return empty, {}, detected, None, selected_contact_sites or station

    combined = pd.concat(frames, ignore_index=True, sort=False)

    # IMPORTANT: use the exact Contact Site selected by the user.
    combined, site_col, site_filter = filter_station(
        combined,
        station,
        selected_contact_sites,
    )

    # Compare the filtered CMDB population with the Local Register.
    results = match_rows(combined, id_sets(local))

    # Reproduce the supplied VBA classification logic.
    def map_vba_status(value):
        status_text = str(value).strip().upper()
        if "SER" in status_text:
            return "In Service"
        elif "STO" in status_text:
            return "In Store"
        elif "RET" in status_text:
            return "Retired"
        elif "LOAN" in status_text:
            return "Loan"
        elif "PRE" in status_text or "PROD" in status_text:
            return "Preproduction"
        return "Other / Unknown"

    results["Status Group"] = results["CMDB Device Status"].map(map_vba_status)

    status_names = [
        "In Service",
        "In Store",
        "Preproduction",
        "Loan",
        "Retired",
        "Other / Unknown",
    ]

    groups = {
        name: results[results["Status Group"] == name].copy()
        for name in status_names
    }

    for name, group in groups.items():
        group["Local Result"] = group["Matched"].map(
            {
                True: "Found in local file",
                False: "Not found in local file",
            }
        )

        if name in ["In Service", "Preproduction"]:
            group["Recommended Action"] = group["Matched"].map(
                {
                    True: (
                        "Confirm physical status. If the device is in storage, "
                        "please call 8910 to change the CMDB status to In Store."
                    ),
                    False: (
                        "Investigate the device location, assigned user, "
                        "contact site and responsible power station."
                    ),
                }
            )
        else:
            group["Recommended Action"] = (
                "Review and validate this CMDB status against the local asset record."
            )

    return results, groups, detected, site_col, site_filter

def excel_report(sheets):
    """Create a safe multi-sheet Excel report."""
    output = io.BytesIO()
    used_names = set()

    def safe_sheet_name(value):
        # Excel forbids: \ / * ? : [ ] and limits titles to 31 characters.
        name = re.sub(r'[\\/*?:\[\]]', '_', str(value)).strip().strip("'")
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


# -----------------------------------------------------------------------------
# HEADER / CONTROLS
# -----------------------------------------------------------------------------
st.title("CMDB Reconciliation & Asset Governance Tool")

power_station = st.selectbox(
    "Select Power Station",
    ["Komati", "Kendal", "Duvha", "Matla", "Tutuka", "Majuba", "Lethabo", "Kusile", "Medupi"],
)
st.info(f"Currently working with: {power_station} Power Station")
st.caption(f"Last updated: {datetime.now().strftime('%d %B %Y %H:%M')}")

with st.sidebar:
    st.header("Matching rules")
    st.markdown(
        "1. Desktop Hostname = Local UID  \n"
        "2. Monitor Asset Tag = Local UID  \n"
        "3. Serial Number  \n"
        "4. Asset Number"
    )
    st.info("For Komati, Contact Site is filtered using MP_MIDDELBURG_KOMATI POWER.")

# -----------------------------------------------------------------------------
# FILE UPLOADS
# -----------------------------------------------------------------------------
st.subheader("1. Upload files")
st.caption(
    f"Upload the latest Desktop CMDB, Monitor CMDB and Local Asset Register for {power_station} Power Station."
)
st.markdown("### 📁 Asset Data Sources")
st.caption(
    "**PC CMDB** includes both desktop computers and laptops. "
    "Monitor CMDB is processed separately."
)

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("#### 🖥️ PC CMDB")
    desktop_file = st.file_uploader(
        "PC CMDB (Desktops & Laptops)", type=["xlsx", "xls"], key="desktop", label_visibility="collapsed"
    )
with c2:
    st.markdown("#### 🖥️ Monitor")
    monitor_file = st.file_uploader(
        "Monitor CMDB", type=["xlsx", "xls"], key="monitor", label_visibility="collapsed"
    )
with c3:
    st.markdown("#### 📋 Local Register")
    local_file = st.file_uploader(
        "Local Consolidated Asset Register",
        type=["xlsx", "xls"],
        key="local",
        label_visibility="collapsed",
    )

if not all([desktop_file, monitor_file, local_file]):
    st.info("Please upload all three Excel files to continue.")
    st.stop()

progress_bar = st.progress(10, text="Checking uploaded files...")

files = {
    "Desktop CMDB": desktop_file,
    "Monitor CMDB": monitor_file,
    "Local Register": local_file,
}
errors = validate_uploads(files)
if errors:
    for error in errors:
        st.error(error)
    st.stop()

# Workbook-role validation. These checks are intentionally specific.
desktop_valid = workbook_has_any_column(
    desktop_file, ["Hostname", "Host Name", "Computer Name", "Device Name"]
)
monitor_valid = workbook_has_any_column(
    monitor_file, ["Asset Tag", "AssetTag", "Asset ID", "Asset Identifier"]
)
local_uid_valid = workbook_has_any_column(
    local_file, ["UID", "Unique ID", "Unique Identifier"]
)
local_serial_valid = workbook_has_any_column(
    local_file, ["Serial Number", "Serial No", "Serial", "S/N"]
)
local_asset_valid = workbook_has_any_column(
    local_file, ["Asset Number", "Asset No", "Equipment Number", "Equipment No"]
)

if not desktop_valid:
    st.error("Incorrect Desktop CMDB file. A Desktop CMDB identifier such as Hostname was not detected.")
    st.stop()
if not monitor_valid:
    st.error("Incorrect Monitor CMDB file. A Monitor identifier such as Asset Tag was not detected.")
    st.stop()
if not (local_uid_valid and local_serial_valid and local_asset_valid):
    missing_fields = []
    if not local_uid_valid:
        missing_fields.append("UID")
    if not local_serial_valid:
        missing_fields.append("Serial Number")
    if not local_asset_valid:
        missing_fields.append("Asset Number")
    st.error(
        "Incorrect Local Asset Register. Expected structure was not detected. "
        f"Missing required field(s): {', '.join(missing_fields)}."
    )
    st.stop()

progress_bar.progress(30, text="Files validated successfully. Preparing worksheets...")

st.markdown("### ✅ File Readiness")
r1, r2, r3 = st.columns(3)
r1.success("✅ Desktop CMDB validated")
r2.success("✅ Monitor CMDB validated")
r3.success("✅ Local Register validated")

# -----------------------------------------------------------------------------
# WORKSHEETS / SCOPE / IDENTIFIERS
# -----------------------------------------------------------------------------
st.subheader("2. Select worksheets")
c1, c2, c3 = st.columns(3)
with c1:
    desktop_sheet = st.selectbox("PC CMDB worksheet", sheet_names(desktop_file))
with c2:
    monitor_sheet = st.selectbox("Monitor worksheet", sheet_names(monitor_file))
with c3:
    local_sheet = st.selectbox("Local worksheet", sheet_names(local_file))

try:
    desktop_df = read_sheet(desktop_file, desktop_sheet)
    monitor_df = read_sheet(monitor_file, monitor_sheet)
    local_df = read_sheet(local_file, local_sheet)
except Exception as error:
    st.error(f"Workbook reading error: {error}")
    st.stop()

progress_bar.progress(55, text="Worksheets loaded. Preparing asset data...")

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

# MULTIPLE_CONTACT_SITES_V1
_default_sites = [_all_sites[_default_index]] if _all_sites else []
selected_contact_sites = st.multiselect(
    "CMDB Contact Site(s)",
    _all_sites,
    default=_default_sites,
    key="selected_contact_sites",
    help="Select one or more Contact Sites belonging to the selected power station.",
)

if not selected_contact_sites:
    st.warning("Select at least one CMDB Contact Site to continue.")
    st.stop()

st.info(
    "CMDB records will be filtered using these exact Contact Sites: "
    + ", ".join(selected_contact_sites)
)


if desktop_df.empty or monitor_df.empty or local_df.empty:
    st.error("One or more selected worksheets are empty.")
    st.stop()

st.subheader("4. Select local assets in scope")
scope_candidates = [
    "Asset Type",
    "Device Type",
    "Equipment Type",
    "Category",
    "Class",
    "Asset Category",
    "Description",
    "In Scope",
    "Scope",
]
scope_options = [NO_FILTER] + list(local_df.columns)
default_scope = find_column(local_df.columns, scope_candidates)
scope_column = st.selectbox(
    "Local scope/filter column",
    scope_options,
    index=scope_options.index(default_scope) if default_scope in scope_options else 0,
)

if scope_column == NO_FILTER:
    local_in_scope = local_df.copy()
    local_excluded = local_df.iloc[0:0].copy()
else:
    values = local_df[scope_column].fillna("(Blank)").astype(str).str.strip()
    available = sorted(values.unique())
    selected = st.multiselect("Include these values", available, default=available)
    local_in_scope = local_df.loc[values.isin(selected)].copy()
    local_excluded = local_df.loc[~values.isin(selected)].copy()

if local_in_scope.empty:
    st.error("No local assets are in scope.")
    st.stop()

primary_desktop = ["Hostname", "Host Name", "Computer Name", "Device Name", "UID"]
primary_monitor = ["Asset Tag", "AssetTag", "Asset ID", "Asset Identifier", "UID"]
primary_local = ["UID", "Unique ID", "Unique Identifier", "Hostname", "Asset Tag"]
serials = ["Serial Number", "Serial No", "Serial", "S/N", "Equipment Serial Number"]
assets = ["Asset Number", "Asset No", "Asset", "Equipment Number", "Equipment No"]

st.subheader("5. Confirm identifier columns")
st.caption("Desktop: Hostname. Monitor: Asset Tag. Local: UID. Serial Number and Asset Number are fallbacks.")

st.markdown("**Desktop CMDB**")
c1, c2, c3 = st.columns(3)
with c1:
    d_primary = selector("PC Hostname", desktop_df, primary_desktop, "dp")
with c2:
    d_serial = selector("Desktop Serial Number", desktop_df, serials, "ds")
with c3:
    d_asset = selector("Desktop Asset Number", desktop_df, assets, "da")

st.markdown("**Monitor CMDB**")
c1, c2, c3 = st.columns(3)
with c1:
    m_primary = selector("Monitor Asset Tag", monitor_df, primary_monitor, "mp")
with c2:
    m_serial = selector("Monitor Serial Number", monitor_df, serials, "ms")
with c3:
    m_asset = selector("Monitor Asset Number", monitor_df, assets, "ma")

st.markdown("**Local Register**")
c1, c2, c3 = st.columns(3)
with c1:
    l_primary = selector("Local UID", local_in_scope, primary_local, "lp")
with c2:
    l_serial = selector("Local Serial Number", local_in_scope, serials, "ls")
with c3:
    l_asset = selector("Local Asset Number", local_in_scope, assets, "la")

with st.expander("Preview data"):
    p1, p2, p3, p4 = st.tabs(["Desktop", "Monitor", "Local in scope", "Local excluded"])
    with p1:
        st.dataframe(desktop_df.head(10), use_container_width=True)
    with p2:
        st.dataframe(monitor_df.head(10), use_container_width=True)
    with p3:
        st.dataframe(local_in_scope.head(10), use_container_width=True)
    with p4:
        st.dataframe(local_excluded.head(10), use_container_width=True)


# -----------------------------------------------------------------------------
# PRE-RECONCILIATION CMDB STATUS SUMMARY
# -----------------------------------------------------------------------------
st.divider()
st.markdown("## 📋 Pre-Reconciliation CMDB Status Summary")
st.caption(
    "The Desktop and Monitor CMDB status totals below are calculated separately "
    "before either source is compared with the Local Asset Register."
)

(
    desktop_pre_status,
    desktop_status_column,
    desktop_status_counts,
    desktop_raw_status_counts,
    desktop_site_column,
    desktop_site_filter,
) = build_precomparison_status_summary(
    desktop_df,
    "Desktop CMDB",
    power_station,
    selected_contact_sites,
)

(
    monitor_pre_status,
    monitor_status_column,
    monitor_status_counts,
    monitor_raw_status_counts,
    monitor_site_column,
    monitor_site_filter,
) = build_precomparison_status_summary(
    monitor_df,
    "Monitor CMDB",
    power_station,
    selected_contact_sites,
)

status_order = [
    "In Service",
    "In Store",
    "Retired",
    "Loan",
    "Preproduction",
    "Other / Unknown",
]

left_summary, right_summary = st.columns(2)

with left_summary:
    st.markdown("### 🖥️ Desktop CMDB Status")
    st.metric("PC records for selected station", len(desktop_pre_status))

    if desktop_status_column:
        st.caption(
            f"Status column: {desktop_status_column} | "
            f"Site filter: {desktop_site_filter}"
        )
       
    else:
        st.warning("No recognised Desktop CMDB status column was detected.")

    if desktop_status_column:
        with st.expander("View exact PC CMDB status values"):
            st.dataframe(
                desktop_raw_status_counts,
                use_container_width=True,
                hide_index=True,
            )

with right_summary:
    st.markdown("### 🖥️ Monitor CMDB Status")
    st.metric("Monitor records for selected station", len(monitor_pre_status))

    if monitor_status_column:
        st.caption(
            f"Status column: {monitor_status_column} | "
            f"Site filter: {monitor_site_filter}"
        )

       
    else:
        st.warning("No recognised Monitor CMDB status column was detected.")

    if monitor_status_column:
        with st.expander("View exact Monitor CMDB status values"):
            st.dataframe(
                monitor_raw_status_counts,
                use_container_width=True,
                hide_index=True,
            )

combined_status_summary = pd.DataFrame(
    {
        "Status": status_order,
        "Desktop": [desktop_status_counts[name] for name in status_order],
        "Monitor": [monitor_status_counts[name] for name in status_order],
    }
)
combined_status_summary["Combined"] = (
    combined_status_summary["Desktop"]
    + combined_status_summary["Monitor"]
)

st.info(
    f"Exact station filter applied before counting: "
    f"Desktop = {desktop_site_filter}; Monitor = {monitor_site_filter}. "
    f"Source totals: Desktop {len(desktop_pre_status):,}; "
    f"Monitor {len(monitor_pre_status):,}."
)

st.markdown("### 📊 Combined CMDB Status Totals")
st.dataframe(
    combined_status_summary,
    use_container_width=True,
    hide_index=True,
)

combined_chart = combined_status_summary.set_index("Status")[["Desktop", "Monitor"]]
st.bar_chart(combined_chart)

with st.expander("View Desktop and Monitor CMDB records before comparison"):
    desktop_tab, monitor_tab = st.tabs(["Desktop CMDB", "Monitor CMDB"])

    with desktop_tab:
        if desktop_status_column:
            desktop_filter = st.selectbox(
                "PC status to view",
                ["All"] + status_order,
                key="desktop_pre_status_filter",
            )
            desktop_view = desktop_pre_status
            if desktop_filter != "All":
                desktop_view = desktop_view[
                    desktop_view["Status Group"] == desktop_filter
                ]
            st.caption(f"{len(desktop_view):,} Desktop CMDB record(s)")
            st.dataframe(desktop_view, use_container_width=True, hide_index=True)
        else:
            st.info("Desktop status details are unavailable because no status column was detected.")

    with monitor_tab:
        if monitor_status_column:
            monitor_filter = st.selectbox(
                "Monitor status to view",
                ["All"] + status_order,
                key="monitor_pre_status_filter",
            )
            monitor_view = monitor_pre_status
            if monitor_filter != "All":
                monitor_view = monitor_view[
                    monitor_view["Status Group"] == monitor_filter
                ]
            st.caption(f"{len(monitor_view):,} Monitor CMDB record(s)")
            st.dataframe(monitor_view, use_container_width=True, hide_index=True)
        else:
            st.info("Monitor status details are unavailable because no status column was detected.")

st.info(
    "These figures describe the CMDB extracts before reconciliation. "
    "Select Run comparison below to compare the Desktop and Monitor records with the Local Asset Register."
)

# -----------------------------------------------------------------------------
# RUN COMPARISON + SESSION STATE
# -----------------------------------------------------------------------------
comparison_signature = hashlib.sha256(
    (
        power_station
        + fingerprint(desktop_file)
        + fingerprint(monitor_file)
        + fingerprint(local_file)
        + str(desktop_sheet)
        + str(monitor_sheet)
        + str(local_sheet)
        + "|".join(sorted(selected_contact_sites))
        + str(scope_column)
        + str(d_primary)
        + str(m_primary)
        + str(l_primary)
    ).encode("utf-8")
).hexdigest()

if st.session_state.get("comparison_signature") != comparison_signature:
    st.session_state.comparison_done = False

if st.button("Run comparison", type="primary", use_container_width=True, key="run_comparison"):
    st.session_state.comparison_done = True
    st.session_state.comparison_signature = comparison_signature

if not st.session_state.get("comparison_done", False):
    st.stop()

progress_bar.progress(75, text="Running CMDB reconciliation...")

try:
    desktop = prepare(
        desktop_df, d_primary, d_serial, d_asset, "Desktop CMDB", "Desktop", "Hostname"
    )
    monitor = prepare(
        monitor_df, m_primary, m_serial, m_asset, "Monitor CMDB", "Monitor", "Asset Tag"
    )
    local = prepare(
        local_in_scope, l_primary, l_serial, l_asset, "Local Register", "Local", "UID"
    )

    cmdb = pd.concat([desktop, monitor], ignore_index=True, sort=False)
    local_results = match_rows(local, id_sets(cmdb))
    cmdb_results = match_rows(cmdb, id_sets(local))

    comparable_local = local_results[local_results["Comparison Key"].ne("")].copy()
    matched = comparable_local[comparable_local["Matched"]].copy()
    missing_cmdb = comparable_local[~comparable_local["Matched"]].copy()
    no_identifier = local_results[local_results["Comparison Key"].eq("")].copy()

    station_cmdb, site_column, station_filter = filter_station(cmdb_results, power_station, selected_contact_sites)
    missing_local = station_cmdb[
        station_cmdb["Comparison Key"].ne("") & (~station_cmdb["Matched"])
    ].copy()

    duplicate_cmdb, cmdb_duplicate_evidence = build_duplicate_investigation(cmdb, "CMDB")
    duplicate_local, local_duplicate_evidence = build_duplicate_investigation(local, "Local Register")
    match_rate = (
        len(matched) / len(comparable_local) * 100 if len(comparable_local) else 0
    )

    (
        all_station_status,
        status_groups,
        detected_status,
        status_site_column,
        status_site_filter,
    ) = build_status_results(desktop, monitor, local, power_station, selected_contact_sites)

except Exception as error:
    st.error(f"Comparison stopped because of a data problem: {error}")
    st.stop()

progress_bar.progress(100, text="Reconciliation completed successfully.")
st.success(f"✅ {power_station} Power Station reconciliation completed successfully.")

# -----------------------------------------------------------------------------
# AUDIT DATA + AUDIT DISPLAY + HISTORY
# -----------------------------------------------------------------------------
reconciliation_date = datetime.now()
reconciliation_id = hashlib.sha256(
    (
        power_station
        + fingerprint(desktop_file)
        + fingerprint(monitor_file)
        + fingerprint(local_file)
    ).encode("utf-8")
).hexdigest()

audit_data = {
    "Power Station": power_station,
    "Contact Site": ", ".join(selected_contact_sites),
    "Reconciliation Date": reconciliation_date.strftime("%d %B %Y %H:%M"),
    "Desktop CMDB File": desktop_file.name,
    "Monitor CMDB File": monitor_file.name,
    "Local Register File": local_file.name,
    "Comparable Local Assets": len(comparable_local),
    "Matched Assets": len(matched),
    "Missing on CMDB": len(missing_cmdb),
    "Missing Locally": len(missing_local),
    "Duplicate CMDB Records": len(duplicate_cmdb),
    "Duplicate Local Records": len(duplicate_local),
    "No Identifier": len(no_identifier),
    "Match Rate": round(match_rate, 2),
    "Reconciliation ID": reconciliation_id,
}

with st.expander("📋 Reconciliation Audit Information", expanded=False):
    st.write(f"**Power Station:** {audit_data['Power Station']}")
    st.write(f"**Reconciliation Date:** {audit_data['Reconciliation Date']}")
    st.write(f"**Desktop CMDB:** {audit_data['Desktop CMDB File']}")
    st.write(f"**Monitor CMDB:** {audit_data['Monitor CMDB File']}")
    st.write(f"**Local Register:** {audit_data['Local Register File']}")
    st.divider()
    a1, a2, a3 = st.columns(3)
    a1.metric("Matched Assets", audit_data["Matched Assets"])
    a2.metric("Missing on CMDB", audit_data["Missing on CMDB"])
    a3.metric("Match Rate", f"{audit_data['Match Rate']:.1f}%")
    a4, a5, a6 = st.columns(3)
    a4.metric("Missing Locally", audit_data["Missing Locally"])
    a5.metric("CMDB Duplicate Identifiers", audit_data["Duplicate CMDB Records"])
    a6.metric("Local Duplicate Identifiers", audit_data["Duplicate Local Records"])
    if audit_data["No Identifier"] > 0:
        st.warning(
            f"{audit_data['No Identifier']} local asset(s) have no usable reconciliation identifier."
        )

history_record = pd.DataFrame([audit_data])
if os.path.exists(HISTORY_FILE):
    try:
        history_df = pd.read_csv(HISTORY_FILE, dtype=str)
    except Exception:
        history_df = pd.DataFrame()
else:
    history_df = pd.DataFrame()

# Repair earlier accidental duplicates in memory and when the next save occurs.
if not history_df.empty and "Reconciliation ID" in history_df.columns:
    history_df = history_df.drop_duplicates(subset=["Reconciliation ID"], keep="last")

already_saved = (
    not history_df.empty
    and "Reconciliation ID" in history_df.columns
    and reconciliation_id in history_df["Reconciliation ID"].astype(str).values
)

st.markdown("### 📚 Reconciliation History")
button_label = (
    "✅ Reconciliation Already Saved"
    if already_saved
    else "💾 Save Reconciliation to History"
)

if st.button(
    button_label,
    type="primary",
    disabled=already_saved,
    key="save_reconciliation_history",
):
    if history_df.empty:
        updated_history = history_record.copy()
    else:
        all_columns = list(
            dict.fromkeys(list(history_df.columns) + list(history_record.columns))
        )
        updated_history = pd.concat(
            [
                history_df.reindex(columns=all_columns),
                history_record.reindex(columns=all_columns),
            ],
            ignore_index=True,
        )
        updated_history = updated_history.drop_duplicates(
            subset=["Reconciliation ID"], keep="last"
        )

    updated_history.to_csv(HISTORY_FILE, index=False)
    st.success(f"✅ {power_station} reconciliation saved to history.")
    history_df = updated_history.astype(str)
    already_saved = True

if already_saved:
    st.info("ℹ️ This reconciliation is already saved in the history.")

if not history_df.empty:
    display_columns = [
        col for col in history_df.columns if col != "Reconciliation ID"
    ]
    st.dataframe(
        history_df[display_columns],
        use_container_width=True,
        hide_index=True,
    )



# Priority CMDB Status requested during the OU IM Departmental Meeting.
st.markdown("## 🎯 Priority CMDB Status")
st.caption(
    "In Service and Preproduction are highlighted as the priority CMDB categories. "
    "In Store, Loan and Retired remain visible for the complete CMDB status picture. "
    "This section does not compare active CMDB assets with the Local Register."
)

_priority_in_service = status_groups.get("In Service", pd.DataFrame())
_priority_preprod = status_groups.get("Preproduction", pd.DataFrame())
_other_in_store = status_groups.get("In Store", pd.DataFrame())
_other_loan = status_groups.get("Loan", pd.DataFrame())
_other_retired = status_groups.get("Retired", pd.DataFrame())

_in_service_total = len(_priority_in_service)
_preprod_total = len(_priority_preprod)
_priority_total = _in_service_total + _preprod_total

p1, p2, p3 = st.columns(3)
p1.metric("In Service", f"{_in_service_total:,}")
p2.metric("Preproduction", f"{_preprod_total:,}")
p3.metric("Priority CMDB Assets", f"{_priority_total:,}")

st.markdown("### Other CMDB Status")
o1, o2, o3 = st.columns(3)
o1.metric("In Store", f"{len(_other_in_store):,}")
o2.metric("Loan", f"{len(_other_loan):,}")
o3.metric("Retired", f"{len(_other_retired):,}")

with st.expander("View Priority CMDB Asset Details"):
    priority_tabs = st.tabs(["In Service", "Preproduction"])
    with priority_tabs[0]:
        st.caption(f"{_in_service_total:,} In Service record(s)")
        st.dataframe(_priority_in_service, use_container_width=True, hide_index=True)
    with priority_tabs[1]:
        st.caption(f"{_preprod_total:,} Preproduction record(s)")
        st.dataframe(_priority_preprod, use_container_width=True, hide_index=True)

with st.expander("View Other CMDB Status Details"):
    other_tabs = st.tabs(["In Store", "Loan", "Retired"])
    with other_tabs[0]:
        st.dataframe(_other_in_store, use_container_width=True, hide_index=True)
    with other_tabs[1]:
        st.dataframe(_other_loan, use_container_width=True, hide_index=True)
    with other_tabs[2]:
        st.dataframe(_other_retired, use_container_width=True, hide_index=True)

st.info(
    "The Local Asset reconciliation below remains a separate control because the "
    "Local Register represents locally managed / stored assets."
)

# -----------------------------------------------------------------------------
# EXECUTIVE DASHBOARD
# -----------------------------------------------------------------------------
st.markdown("## 📊 Reconciliation Dashboard")
a, b, c, d, e = st.columns(5)
a.metric("Local in scope", len(local))
b.metric("Matched", len(matched))
c.metric("Missing on CMDB", len(missing_cmdb))
d.metric("Missing locally", len(missing_local))
e.metric("Match rate", f"{match_rate:.1f}%")

# CMDB health
st.subheader("CMDB Health Status")
if match_rate >= 95:
    st.success(f"🟢 HEALTHY CMDB ({match_rate:.1f}%)")
elif match_rate >= 80:
    st.warning(f"🟡 ATTENTION REQUIRED ({match_rate:.1f}%)")
else:
    st.error(f"🔴 CRITICAL DATA QUALITY ISSUE ({match_rate:.1f}%)")

# Reconciliation visuals
visual_left, visual_right = st.columns(2)
status_chart = pd.DataFrame(
    {
        "Status": ["Matched", "Missing on CMDB", "No Identifier"],
        "Count": [len(matched), len(missing_cmdb), len(no_identifier)],
    }
)
with visual_left:
    fig_recon = px.pie(
        status_chart,
        names="Status",
        values="Count",
        title="Asset Reconciliation Status",
    )
    st.plotly_chart(fig_recon, use_container_width=True)

with visual_right:
    if not matched.empty:
        methods = matched["Matched By"].value_counts().reset_index()
        methods.columns = ["Matching Method", "Count"]
        fig_method = px.bar(
            methods,
            x="Matching Method",
            y="Count",
            title="Match Method Analysis",
            text_auto=True,
        )
        st.plotly_chart(fig_method, use_container_width=True)

quality_df = pd.DataFrame(
    {
        "Metric": [
            "Missing on CMDB",
            "No Identifier",
            "CMDB Duplicate Identifiers",
            "Local Duplicate Identifiers",
        ],
        "Count": [
            len(missing_cmdb),
            len(no_identifier),
            len(duplicate_cmdb),
            len(duplicate_local),
        ],
    }
)
st.subheader("CMDB Data Quality Dashboard")
st.bar_chart(quality_df.set_index("Metric"))

# -----------------------------------------------------------------------------
# STATUS VALIDATION
# -----------------------------------------------------------------------------
st.divider()
st.markdown(f"## 🔍 {power_station} CMDB Status Overview")
if not detected_status:
    st.warning("No CMDB status column was detected.")
else:
    st.info(
        f"Contact Site filter used: {status_site_filter} | Column used: {status_site_column}"
    )
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("All In Service", len(status_groups["In Service"]))
    s2.metric("All Preproduction", len(status_groups["Preproduction"]))
    s3.metric("All Loan", len(status_groups["Loan"]))
    s4.metric(
    "All Retired",
    len(status_groups.get("Retired", pd.DataFrame()))
)
    totals = pd.DataFrame(
        {
            "Status": ["In Service", "Preproduction", "Loan", "Retired"],
            "Number of devices": [
            len(status_groups.get(name, pd.DataFrame()))
                for name in ["In Service", "Preproduction", "Loan", "Retired"]
            ],
        }
    )
    fig = px.bar(
        totals,
        x="Status",
        y="Number of devices",
        title=f"{power_station} CMDB Device Status Totals",
        text_auto=True,
    )
    st.plotly_chart(fig, use_container_width=True)

    status_tabs = st.tabs(
    [
        "In Service",
        "Preproduction",
        "Loan",
        "Retired"
    ]
)

for tab, name in zip(
    status_tabs,
    [
        "In Service",
        "Preproduction",
        "Loan",
        "Retired"
    ]
):
    with tab:

        group = status_groups.get(
            name,
            pd.DataFrame()
        )

        f1, f2, f3 = st.columns(3)

        f1.metric(
            f"All {name}",
            len(group)
        )

        if not group.empty:

            f2.metric(
                "Found locally",
                int(group["Matched"].sum())
            )

            f3.metric(
                "Not found locally",
                int((~group["Matched"]).sum())
            )

            st.dataframe(
                group,
                use_container_width=True,
                hide_index=True
            )

        else:

            f2.metric(
                "Found locally",
                0
            )

            f3.metric(
                "Not found locally",
                0
            )

            st.info(
                f"No {name} records were found "
                "for the selected Contact Site."
            )

# KOMATI_CMDB_ASSET_TEMPLATE_V2
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
    ws["A2"] = f"CMDB Contact Sites: {', '.join(selected_contact_sites)}"
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
        ("Contact Site", ", ".join(selected_contact_sites)),
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

# -----------------------------------------------------------------------------
# DETAILED RESULTS / EXPORT
# -----------------------------------------------------------------------------
st.markdown("## 🔍 Duplicate Comparison Evidence")
st.info(
    "Duplicates are identified by comparing repeated Primary Identifiers, Serial Numbers, "
    "and Asset Numbers. One row represents one repeated identifier. "
    "Affected Records shows how many source rows share that identifier."
)

legend1, legend2, legend3, legend4 = st.columns(4)
legend1.error("🔴 Conflict: supporting fields differ")
legend2.warning("🟠 Repeated identical: likely repeated row")
legend3.info("🔵 Cross-source: appears in PC and Monitor CMDB")
legend4.success("🟢 Reviewed: reserved for future review workflow")

dup1, dup2, dup3, dup4 = st.columns(4)
dup1.metric("CMDB Duplicate Identifiers", len(duplicate_cmdb))
dup2.metric(
    "Affected CMDB Records",
    int(duplicate_cmdb["Affected Records"].sum()) if not duplicate_cmdb.empty else 0,
)
dup3.metric("Local Duplicate Identifiers", len(duplicate_local))
dup4.metric(
    "Affected Local Records",
    int(duplicate_local["Affected Records"].sum()) if not duplicate_local.empty else 0,
)

cmdb_dup_tab, local_dup_tab = st.tabs(
    ["CMDB Duplicate Evidence", "Local Duplicate Evidence"]
)

with cmdb_dup_tab:
    if duplicate_cmdb.empty:
        st.success("No repeated CMDB identifiers were detected.")
    else:
        cmdb_display = duplicate_cmdb.drop(columns=["Issue Key"], errors="ignore")
        st.dataframe(
            cmdb_display.style.apply(duplicate_table_style, axis=1),
            use_container_width=True,
            hide_index=True,
        )
        selected_cmdb_issue = st.selectbox(
            "Select a CMDB duplicate to view the records compared",
            duplicate_cmdb["Issue Key"].tolist(),
            format_func=lambda key: (
                duplicate_cmdb.loc[
                    duplicate_cmdb["Issue Key"].eq(key),
                    ["Risk", "Identifier Type", "Identifier Value"],
                ].astype(str).agg(" | ".join, axis=1).iloc[0]
            ),
            key="selected_cmdb_duplicate_issue",
        )
        selected_row = duplicate_cmdb.loc[
            duplicate_cmdb["Issue Key"].eq(selected_cmdb_issue)
        ].iloc[0]
        st.markdown(
            f"**Why flagged:** {selected_row['Why Flagged']}  \n"
            f"**Comparison used:** {selected_row['Identifier Type']} = "
            f"`{selected_row['Identifier Value']}`"
        )
        st.dataframe(
            cmdb_duplicate_evidence[selected_cmdb_issue],
            use_container_width=True,
            hide_index=True,
        )

with local_dup_tab:
    if duplicate_local.empty:
        st.success("No repeated Local Register identifiers were detected.")
    else:
        local_display = duplicate_local.drop(columns=["Issue Key"], errors="ignore")
        st.dataframe(
            local_display.style.apply(duplicate_table_style, axis=1),
            use_container_width=True,
            hide_index=True,
        )
        selected_local_issue = st.selectbox(
            "Select a Local duplicate to view the records compared",
            duplicate_local["Issue Key"].tolist(),
            format_func=lambda key: (
                duplicate_local.loc[
                    duplicate_local["Issue Key"].eq(key),
                    ["Risk", "Identifier Type", "Identifier Value"],
                ].astype(str).agg(" | ".join, axis=1).iloc[0]
            ),
            key="selected_local_duplicate_issue",
        )
        selected_local_row = duplicate_local.loc[
            duplicate_local["Issue Key"].eq(selected_local_issue)
        ].iloc[0]
        st.markdown(
            f"**Why flagged:** {selected_local_row['Why Flagged']}  \n"
            f"**Comparison used:** {selected_local_row['Identifier Type']} = "
            f"`{selected_local_row['Identifier Value']}`"
        )
        st.dataframe(
            local_duplicate_evidence[selected_local_issue],
            use_container_width=True,
            hide_index=True,
        )

# MULTI_INSERVICE_DEVICE_CHECK_V2
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

st.subheader("Detailed reconciliation results")
st.info(
    "Duplicate tabs now show one row per repeated identifier, not every source row. "
    "A repeated identifier is an investigation item and does not automatically mean "
    "that multiple physical devices exist or that a record must be deleted."
)
tabs = st.tabs(
    [
        "Matches",
        "Missing on CMDB",
        "Missing locally",
        "No identifier",
        "CMDB duplicate identifiers",
        "Local duplicate identifiers",
    ]
)
frames = [
    matched,
    missing_cmdb,
    missing_local,
    no_identifier,
    duplicate_cmdb,
    duplicate_local,
]
for tab, frame in zip(tabs, frames):
    with tab:
        st.caption(f"{len(frame):,} row(s)")
        st.dataframe(frame, use_container_width=True, hide_index=True)

summary = pd.DataFrame(
    {
        "Measure": [
            "Power Station",
            "Contact Site filter",
            "Local in scope",
            "Excluded local assets",
            "All In Service",
            "All Preproduction",
            "All Loan",
            "All Retired",
            "Matched",
            "Missing on CMDB",
            "Missing locally",
            "No identifier",
            "CMDB duplicate identifiers",
            "Local duplicate identifiers",
            "Match rate",
        ],
        "Value": [
            power_station,
            status_site_filter,
            len(local),
            len(local_excluded),
            len(status_groups.get("In Service", [])),
            len(status_groups.get("Preproduction", [])),
            len(status_groups.get("Loan", [])),
            len(status_groups.get("Retired", [])),
            len(matched),
            len(missing_cmdb),
            len(missing_local),
            len(no_identifier),
            len(duplicate_cmdb),
            len(duplicate_local),
            round(match_rate, 2),
        ],
    }
)

sheets = {
    "Summary": summary,
    "Matches": matched,
    "Missing on CMDB": missing_cmdb,
    "Missing Locally": missing_local,
    "No Identifier": no_identifier,
    "CMDB Duplicate Identifiers": duplicate_cmdb,
    "Local Duplicate Identifiers": duplicate_local,
    "Combined CMDB": cmdb_results,
    "Local In Scope": local_results,
}
for name, frame in status_groups.items():
    sheets[name] = frame

report = excel_report(sheets)
st.subheader("Download report")
st.download_button(
    "Download updated CMDB reconciliation report",
    data=report,
    file_name=f"{power_station}_CMDB_Reconciliation_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

if not history_df.empty:
    history_csv = history_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download reconciliation history for Power BI",
        data=history_csv,
        file_name="reconciliation_history_powerbi.csv",
        mime="text/csv",
        use_container_width=True,
        key="download_history_powerbi",
    )

st.divider()
st.caption(
    "CMDB Reconciliation & Asset Governance Tool | Python + Streamlit | Matching priority: Desktop Hostname / Monitor Asset Tag = Local UID, then Serial Number, then Asset Number."
)
