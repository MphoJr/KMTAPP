from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name("app.py")
BACKUP = Path(__file__).with_name("app_before_status_count_fix.py")

if not APP.exists():
    raise SystemExit("app.py was not found. Put this file in the KomatiAssetApp folder next to app.py.")

text = APP.read_text(encoding="utf-8")

# 1. Replace the power-station filter with an exact Contact Site match for Komati.
filter_pattern = re.compile(
    r"def filter_station\(df, station\):.*?(?=\ndef build_precomparison_status_summary\(|\ndef build_status_results\()",
    re.S,
)

new_filter = '''def filter_station(df, station):
    """Filter CMDB rows to the selected power station using exact site values."""
    site_col = find_column(
        df.columns,
        ["Contact Site", "Site", "Location", "Physical Location", "Power Station"],
    )

    if not site_col:
        return df.copy(), None, None

    station_filters = {
        "Komati": "MP_MIDDELBURG_KOMATI POWER STATION",
        "Matla": "MP_KRIEL_MATLA POWER STATION",
        "Duvha": "MP_WITBANK_DUVHA POWER STATION",
        "Kusile": "MP_WITBANK_KUSILE POWER STATION",
        "Lethabo": "FS_METSIMAHOLO_LETHABO POWER STATION",
    }

    exact_site = station_filters.get(station)
    cleaned_site = df[site_col].fillna("").astype(str).str.strip().str.upper()

    if exact_site:
        mask = cleaned_site.eq(exact_site.upper())
        return df.loc[mask].copy(), site_col, exact_site

    # For stations whose exact CMDB value has not yet been configured,
    # use a clear station-name match and report the actual filter used.
    mask = cleaned_site.str.contains(station.upper(), na=False, regex=False)
    return df.loc[mask].copy(), site_col, station


'''

if not filter_pattern.search(text):
    raise SystemExit("Could not find filter_station() in app.py. No changes were made.")

text = filter_pattern.sub(new_filter, text, count=1)

# 2. Replace the pre-comparison status function so it preserves and reports raw CMDB values.
status_pattern = re.compile(
    r"def build_precomparison_status_summary\(.*?(?=\ndef build_status_results\()",
    re.S,
)

new_status_function = '''def build_precomparison_status_summary(frame, source_name, station):
    """Build an exact pre-reconciliation status report for one CMDB source."""
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

    empty_counts = {label: 0 for label in labels}
    empty_raw = pd.DataFrame(columns=["Raw CMDB Status", "Count"])

    if not status_column:
        return (
            filtered,
            status_column,
            empty_counts,
            empty_raw,
            site_column,
            site_filter,
        )

    result = filtered.copy()
    result["Raw CMDB Status"] = (
        result[status_column]
        .fillna("(Blank)")
        .astype(str)
        .str.strip()
        .replace({"": "(Blank)"})
    )

    result["CMDB Status Key"] = (
        result["Raw CMDB Status"]
        .str.lower()
        .str.replace(r"[^a-z]", "", regex=True)
    )

    # Exact, transparent mapping. Anything else remains visible as Other / Unknown.
    status_map = {
        "In Service": {"inservice", "active", "deployed"},
        "In Store": {"instore", "stored", "stock", "warehouse"},
        "Preproduction": {"preproduction", "preprod", "inpreproduction"},
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

    raw_counts = (
        result["Raw CMDB Status"]
        .value_counts(dropna=False)
        .rename_axis("Raw CMDB Status")
        .reset_index(name="Count")
    )

    result["CMDB Source"] = source_name
    return result, status_column, counts, raw_counts, site_column, site_filter


'''

if not status_pattern.search(text):
    raise SystemExit(
        "The installed Pre-Reconciliation Status feature was not found. "
        "Run add_precomparison_status.py first."
    )

text = status_pattern.sub(new_status_function, text, count=1)

# 3. Update the two function-result unpacking blocks to include raw status tables.
text = text.replace(
'''    desktop_status_counts,
    desktop_site_column,''',
'''    desktop_status_counts,
    desktop_raw_status_counts,
    desktop_site_column,''',
1,
)
text = text.replace(
'''    monitor_status_counts,
    monitor_site_column,''',
'''    monitor_status_counts,
    monitor_raw_status_counts,
    monitor_site_column,''',
1,
)

# 4. Add the raw-value tables directly beneath each source summary.
desktop_anchor = '''    else:
        st.warning("No recognised Desktop CMDB status column was detected.")

with right_summary:'''
desktop_replacement = '''    else:
        st.warning("No recognised Desktop CMDB status column was detected.")

    if desktop_status_column:
        with st.expander("View exact Desktop CMDB status values"):
            st.dataframe(
                desktop_raw_status_counts,
                use_container_width=True,
                hide_index=True,
            )

with right_summary:'''

monitor_anchor = '''    else:
        st.warning("No recognised Monitor CMDB status column was detected.")

combined_status_summary = pd.DataFrame('''
monitor_replacement = '''    else:
        st.warning("No recognised Monitor CMDB status column was detected.")

    if monitor_status_column:
        with st.expander("View exact Monitor CMDB status values"):
            st.dataframe(
                monitor_raw_status_counts,
                use_container_width=True,
                hide_index=True,
            )

combined_status_summary = pd.DataFrame('''

if desktop_anchor not in text or monitor_anchor not in text:
    raise SystemExit("Could not find the status-card insertion points. No changes were made.")

text = text.replace(desktop_anchor, desktop_replacement, 1)
text = text.replace(monitor_anchor, monitor_replacement, 1)

# 5. Add a visible validation note with exact source totals.
combined_anchor = '''st.markdown("### 📊 Combined CMDB Status Totals")'''
combined_replacement = '''st.info(
    f"Exact station filter applied before counting: "
    f"Desktop = {desktop_site_filter}; Monitor = {monitor_site_filter}. "
    f"Source totals: Desktop {len(desktop_pre_status):,}; "
    f"Monitor {len(monitor_pre_status):,}."
)

st.markdown("### 📊 Combined CMDB Status Totals")'''

if combined_anchor not in text:
    raise SystemExit("Could not find the combined-summary insertion point. No changes were made.")
text = text.replace(combined_anchor, combined_replacement, 1)

# Validate the modified application before writing it.
compile(text, str(APP), "exec")
shutil.copy2(APP, BACKUP)
APP.write_text(text, encoding="utf-8")

print("Status counting fix installed successfully.")
print(f"Backup created: {BACKUP.name}")
print("Komati now uses exact Contact Site: MP_MIDDELBURG_KOMATI POWER STATION")
print("Desktop and Monitor now show their exact raw CMDB status values separately.")
