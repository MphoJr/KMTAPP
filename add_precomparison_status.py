from pathlib import Path
import shutil

APP = Path(__file__).with_name("app.py")
BACKUP = Path(__file__).with_name("app_before_precomparison_status.py")

if not APP.exists():
    raise SystemExit("app.py was not found. Put this patch file inside the KomatiAssetApp folder, next to app.py.")

text = APP.read_text(encoding="utf-8")

if "def build_precomparison_status_summary(" in text:
    raise SystemExit("The Pre-Reconciliation Status Summary is already installed. No changes were made.")

function_marker = "def build_status_results(desktop, monitor, local, station):"
ui_marker = "# -----------------------------------------------------------------------------\n# RUN COMPARISON + SESSION STATE"

if function_marker not in text:
    raise SystemExit("Could not find build_status_results() in app.py. No changes were made.")
if ui_marker not in text:
    raise SystemExit("Could not find the Run Comparison section in app.py. No changes were made.")

function_code = r'''
def build_precomparison_status_summary(frame, source_name, station):
    """Return station-filtered CMDB status counts before local reconciliation."""
    status_candidates = [
        "Device Status",
        "Status",
        "Install Status",
        "Operational Status",
        "Asset Status",
        "Lifecycle Status",
        "Configuration Item Status",
    ]

    filtered, site_column, site_filter = filter_station(frame, station)
    status_column = find_column(filtered.columns, status_candidates)

    labels = [
        "In Service",
        "In Store",
        "Preproduction",
        "Loan",
        "Retired",
        "Other / Unknown",
    ]

    if not status_column:
        counts = {label: 0 for label in labels}
        return filtered, status_column, counts, site_column, site_filter

    result = filtered.copy()
    result["CMDB Status Raw"] = (
        result[status_column].fillna("").astype(str).str.strip()
    )
    result["CMDB Status Key"] = (
        result["CMDB Status Raw"]
        .str.lower()
        .str.replace(r"[^a-z]", "", regex=True)
    )

    status_map = {
        "In Service": {"inservice", "active", "deployed"},
        "In Store": {"instore", "stored", "stock", "warehouse"},
        "Preproduction": {"preproduction", "preprod"},
        "Loan": {"loan", "onloan", "loaned"},
        "Retired": {"retired", "retire"},
    }

    result["Status Group"] = "Other / Unknown"
    for label, keys in status_map.items():
        result.loc[result["CMDB Status Key"].isin(keys), "Status Group"] = label

    counts = {
        label: int((result["Status Group"] == label).sum())
        for label in labels
    }
    result["CMDB Source"] = source_name
    return result, status_column, counts, site_column, site_filter


'''

ui_code = r'''
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
    desktop_site_column,
    desktop_site_filter,
) = build_precomparison_status_summary(
    desktop_df,
    "Desktop CMDB",
    power_station,
)

(
    monitor_pre_status,
    monitor_status_column,
    monitor_status_counts,
    monitor_site_column,
    monitor_site_filter,
) = build_precomparison_status_summary(
    monitor_df,
    "Monitor CMDB",
    power_station,
)

status_order = [
    "In Service",
    "In Store",
    "Preproduction",
    "Loan",
    "Retired",
    "Other / Unknown",
]

left_summary, right_summary = st.columns(2)

with left_summary:
    st.markdown("### 🖥️ Desktop CMDB Status")
    st.metric("Desktop records for selected station", len(desktop_pre_status))

    if desktop_status_column:
        st.caption(
            f"Status column: {desktop_status_column} | "
            f"Site filter: {desktop_site_filter}"
        )
        d1, d2, d3 = st.columns(3)
        d1.metric("In Service", desktop_status_counts["In Service"])
        d2.metric("In Store", desktop_status_counts["In Store"])
        d3.metric("Preproduction", desktop_status_counts["Preproduction"])
        d4, d5, d6 = st.columns(3)
        d4.metric("Loan", desktop_status_counts["Loan"])
        d5.metric("Retired", desktop_status_counts["Retired"])
        d6.metric("Other / Unknown", desktop_status_counts["Other / Unknown"])
    else:
        st.warning("No recognised Desktop CMDB status column was detected.")

with right_summary:
    st.markdown("### 🖥️ Monitor CMDB Status")
    st.metric("Monitor records for selected station", len(monitor_pre_status))

    if monitor_status_column:
        st.caption(
            f"Status column: {monitor_status_column} | "
            f"Site filter: {monitor_site_filter}"
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("In Service", monitor_status_counts["In Service"])
        m2.metric("In Store", monitor_status_counts["In Store"])
        m3.metric("Preproduction", monitor_status_counts["Preproduction"])
        m4, m5, m6 = st.columns(3)
        m4.metric("Loan", monitor_status_counts["Loan"])
        m5.metric("Retired", monitor_status_counts["Retired"])
        m6.metric("Other / Unknown", monitor_status_counts["Other / Unknown"])
    else:
        st.warning("No recognised Monitor CMDB status column was detected.")

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
                "Desktop status to view",
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

'''

updated = text.replace(function_marker, function_code + function_marker, 1)
updated = updated.replace(ui_marker, ui_code + ui_marker, 1)

# Validate syntax before touching the working app.
compile(updated, str(APP), "exec")

shutil.copy2(APP, BACKUP)
APP.write_text(updated, encoding="utf-8")

print("Pre-Reconciliation CMDB Status Summary installed successfully.")
print(f"Backup created: {BACKUP.name}")
print("Start the app normally and test the Desktop and Monitor summaries before Run comparison.")
