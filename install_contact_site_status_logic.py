from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name("app.py")
BACKUP = Path(__file__).with_name("app_before_contact_site_status_logic.py")

if not APP.exists():
    raise SystemExit("app.py was not found. Put this installer next to app.py in KomatiAssetApp.")

text = APP.read_text(encoding="utf-8")

# Remove any earlier Komati-specific Loan reclassification block if present.
text = re.sub(
    r"\n\s*# KOMATI_DESKTOP_LOAN_RULE_V1.*?(?=\n\s*result\[\"CMDB Source\"\] = source_name)",
    "",
    text,
    count=1,
    flags=re.S,
)

pattern = re.compile(
    r"def build_precomparison_status_summary\(.*?(?=\ndef build_status_results\()",
    re.S,
)

replacement = '''def build_precomparison_status_summary(frame, source_name, station):
    """Filter by selected station Contact Site only, then count all statuses."""
    # Desktop uses Device Status. Monitor uses Status.
    status_candidates = (
        ["Device Status"]
        if source_name == "Desktop CMDB"
        else ["Status"]
    )

    station_rows, site_column, site_filter = filter_station(frame, station)
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


'''

if not pattern.search(text):
    raise SystemExit(
        "Could not find build_precomparison_status_summary() in app.py. No changes were made."
    )

updated = pattern.sub(replacement, text, count=1)

# Ensure the summary order follows the VBA report order.
updated = re.sub(
    r"status_order = \[.*?\]",
    '''status_order = [
    "In Service",
    "In Store",
    "Retired",
    "Loan",
    "Preproduction",
    "Other / Unknown",
]''',
    updated,
    count=1,
    flags=re.S,
)

compile(updated, str(APP), "exec")
shutil.copy2(APP, BACKUP)
APP.write_text(updated, encoding="utf-8")

print("Contact Site-only dynamic status logic installed successfully.")
print(f"Backup created: {BACKUP.name}")
print("Desktop uses Device Status; Monitor uses Status.")
print("All five statuses plus Other / Unknown are calculated automatically.")
print("No Komati totals or Cost Centre rules are hard-coded.")
