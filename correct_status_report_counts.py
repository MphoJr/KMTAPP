from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name("app.py")
BACKUP = Path(__file__).with_name("app_before_final_status_correction.py")

if not APP.exists():
    raise SystemExit("app.py was not found. Put this file next to app.py in KomatiAssetApp.")

text = APP.read_text(encoding="utf-8")

pattern = re.compile(
    r"def build_precomparison_status_summary\(.*?(?=\ndef build_status_results\()",
    re.S,
)

replacement = '''def build_precomparison_status_summary(frame, source_name, station):
    """Create the station status report before local reconciliation.

    The source is filtered to the exact station, duplicate CMDB assets are removed,
    and the original CMDB status wording is mapped to the five reporting groups.
    """
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
        "Retired",
        "Loan",
        "Preproduction",
        "Other / Unknown",
    ]
    zero_counts = {label: 0 for label in labels}
    empty_raw = pd.DataFrame(columns=["Raw CMDB Status", "Count", "Mapped Status"])

    if not status_column:
        return filtered, status_column, zero_counts, empty_raw, site_column, site_filter

    result = filtered.copy()

    # Match the Excel status report behaviour by counting one CMDB asset once.
    # Desktop prefers Hostname; Monitor prefers Asset Tag. Serial Number is fallback.
    if source_name == "Desktop CMDB":
        identity_candidates = [
            "Hostname", "Host Name", "Computer Name", "Device Name",
            "Asset Tag", "Serial Number", "Serial No"
        ]
    else:
        identity_candidates = [
            "Asset Tag", "AssetTag", "Asset ID", "Asset Identifier",
            "Serial Number", "Serial No"
        ]

    identity_column = find_column(result.columns, identity_candidates)
    if identity_column:
        result["Status Report Identity"] = normalize(result[identity_column])
        with_id = result[result["Status Report Identity"].ne("")].drop_duplicates(
            subset=["Status Report Identity"], keep="last"
        )
        without_id = result[result["Status Report Identity"].eq("")]
        result = pd.concat([with_id, without_id], ignore_index=True, sort=False)

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

    def map_status(key):
        # Order matters: preproduction must be checked before generic production terms.
        if "preprod" in key or "preproduction" in key:
            return "Preproduction"
        if "instore" in key or "storage" in key or "stored" in key or key == "store":
            return "In Store"
        if "loan" in key:
            return "Loan"
        if "retir" in key:
            return "Retired"
        if key in {"inservice", "active", "deployed"} or "inservice" in key:
            return "In Service"
        return "Other / Unknown"

    result["Status Group"] = result["CMDB Status Key"].map(map_status)
    result["CMDB Source"] = source_name

    counts = {
        label: int((result["Status Group"] == label).sum())
        for label in labels
    }

    raw_counts = (
        result.groupby(["Raw CMDB Status", "Status Group"], dropna=False)
        .size()
        .reset_index(name="Count")
        .rename(columns={"Status Group": "Mapped Status"})
        .sort_values(["Mapped Status", "Raw CMDB Status"])
        .reset_index(drop=True)
    )

    return result, status_column, counts, raw_counts, site_column, site_filter


'''

if not pattern.search(text):
    raise SystemExit(
        "Could not find build_precomparison_status_summary(). "
        "Install the pre-comparison feature first."
    )

updated = pattern.sub(replacement, text, count=1)

# Ensure any existing display order matches the requested Asset Status Report order.
updated = updated.replace(
'''status_order = [
    "In Service",
    "In Store",
    "Preproduction",
    "Loan",
    "Retired",
    "Other / Unknown",
]''',
'''status_order = [
    "In Service",
    "In Store",
    "Retired",
    "Loan",
    "Preproduction",
    "Other / Unknown",
]'''
)

compile(updated, str(APP), "exec")
shutil.copy2(APP, BACKUP)
APP.write_text(updated, encoding="utf-8")

print("Final status-report correction installed successfully.")
print(f"Backup created: {BACKUP.name}")
print("Desktop and Monitor now use exact station filtering, unique asset counting, and flexible status mapping.")
