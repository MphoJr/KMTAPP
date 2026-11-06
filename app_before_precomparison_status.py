import hashlib
import io
import os
import re
from datetime import datetime

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


def filter_station(df, station):
    site_col = find_column(
        df.columns,
        ["Contact Site", "Site", "Location", "Physical Location", "Power Station"],
    )
    if not site_col:
        return df.copy(), None, station

    station_filters = {"Komati": "MP_MIDDELBURG_KOMATI POWER"}
    site_filter = station_filters.get(station, station)
    mask = (
        df[site_col]
        .fillna("")
        .astype(str)
        .str.contains(site_filter, case=False, na=False, regex=False)
    )
    return df.loc[mask].copy(), site_col, site_filter


def build_status_results(desktop, monitor, local, station):
    candidates = [
        "Device Status",
        "Status",
        "Install Status",
        "Operational Status",
        "Asset Status",
        "Lifecycle Status",
    ]
    frames, detected = [], []
    for frame, source in [(desktop, "Desktop CMDB"), (monitor, "Monitor CMDB")]:
        column = find_column(frame.columns, candidates)
        if column:
            item = frame.copy()
            item["CMDB Device Status"] = item[column].fillna("").astype(str).str.strip()
            item["Status Source Column"] = column
            frames.append(item)
            detected.append(f"{source}: {column}")

    empty = desktop.iloc[0:0].copy()
    if not frames:
        return empty, {}, detected, None, station

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined, site_col, site_filter = filter_station(combined, station)
    results = match_rows(combined, id_sets(local))
    results["Status Key"] = (
        results["CMDB Device Status"]
        .fillna("")
        .astype(str)
        .str.lower()
        .str.replace(r"[^a-z]", "", regex=True)
    )

    groups = {
        "In Service": results[
            results["Status Key"].isin(["inservice", "active", "deployed"])
        ].copy(),
        "Preproduction": results[
            results["Status Key"].isin(["preproduction", "preprod"])
        ].copy(),
        "Loan": results[
            results["Status Key"].isin(["loan", "onloan", "loaned"])
        ].copy(),
        "Retired": results[
            results["Status Key"].isin(["retired", "retire"])
        ].copy(),
    }

    for name, group in groups.items():
        group["Local Result"] = group["Matched"].map(
            {True: "Found in local file", False: "Not found in local file"}
        )
        if name in ["In Service", "Preproduction"]:
            group["Recommended Action"] = group["Matched"].map(
                {
                    True: "Confirm physical status. If the device is in storage, please call 8910 to change the CMDB status to In Store.",
                    False: "Investigate the device location, assigned user, contact site and responsible power station.",
                }
            )
        else:
            group["Recommended Action"] = (
                "Review and validate this CMDB status against the local asset record."
            )

    return results, groups, detected, site_col, site_filter


def excel_report(sheets):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, index=False, sheet_name=name[:31])
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for cells in worksheet.columns:
                width = min(max(len(str(cell.value or "")) for cell in cells) + 2, 45)
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

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("#### 🖥️ Desktop")
    desktop_file = st.file_uploader(
        "Desktop CMDB", type=["xlsx", "xls"], key="desktop", label_visibility="collapsed"
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
    desktop_sheet = st.selectbox("Desktop worksheet", sheet_names(desktop_file))
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

if desktop_df.empty or monitor_df.empty or local_df.empty:
    st.error("One or more selected worksheets are empty.")
    st.stop()

st.subheader("3. Select local assets in scope")
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

st.subheader("4. Confirm identifier columns")
st.caption("Desktop: Hostname. Monitor: Asset Tag. Local: UID. Serial Number and Asset Number are fallbacks.")

st.markdown("**Desktop CMDB**")
c1, c2, c3 = st.columns(3)
with c1:
    d_primary = selector("Desktop Hostname", desktop_df, primary_desktop, "dp")
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

    station_cmdb, site_column, station_filter = filter_station(cmdb_results, power_station)
    missing_local = station_cmdb[
        station_cmdb["Comparison Key"].ne("") & (~station_cmdb["Matched"])
    ].copy()

    duplicate_cmdb = duplicate_rows(cmdb)
    duplicate_local = duplicate_rows(local)
    match_rate = (
        len(matched) / len(comparable_local) * 100 if len(comparable_local) else 0
    )

    (
        all_station_status,
        status_groups,
        detected_status,
        status_site_column,
        status_site_filter,
    ) = build_status_results(desktop, monitor, local, power_station)

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
    a5.metric("CMDB Duplicates", audit_data["Duplicate CMDB Records"])
    a6.metric("Local Duplicates", audit_data["Duplicate Local Records"])
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
            "CMDB Duplicates",
            "Local Duplicates",
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
    s4.metric("All Retired", len(status_groups["Retired"]))

    totals = pd.DataFrame(
        {
            "Status": ["In Service", "Preproduction", "Loan", "Retired"],
            "Number of devices": [
                len(status_groups[name])
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

    status_tabs = st.tabs(["In Service", "Preproduction", "Loan", "Retired"])
    for tab, name in zip(
        status_tabs, ["In Service", "Preproduction", "Loan", "Retired"]
    ):
        with tab:
            group = status_groups[name]
            f1, f2, f3 = st.columns(3)
            f1.metric(f"All {name}", len(group))
            f2.metric(
                "Found locally", int(group["Matched"].sum()) if not group.empty else 0
            )
            f3.metric(
                "Not found locally",
                int((~group["Matched"]).sum()) if not group.empty else 0,
            )
            st.dataframe(group, use_container_width=True, hide_index=True)

# -----------------------------------------------------------------------------
# DETAILED RESULTS / EXPORT
# -----------------------------------------------------------------------------
st.subheader("Detailed reconciliation results")
tabs = st.tabs(
    [
        "Matches",
        "Missing on CMDB",
        "Missing locally",
        "No identifier",
        "CMDB duplicates",
        "Local duplicates",
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
            "CMDB duplicate rows",
            "Local duplicate rows",
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
    "CMDB Duplicates": duplicate_cmdb,
    "Local Duplicates": duplicate_local,
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
