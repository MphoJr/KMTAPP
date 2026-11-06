from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_broken_before_emergency_repair.py')

if not APP.exists():
    raise SystemExit('app.py not found. Put repair_app_now.py in the KomatiAssetApp folder next to app.py.')

text = APP.read_text(encoding='utf-8')
shutil.copy2(APP, BACKUP)

# Replace the entire damaged function. This avoids manual indentation repairs.
pattern = re.compile(
    r'def build_status_results\(.*?(?=\ndef excel_report\(sheets\):)',
    re.S,
)

replacement = '''def build_status_results(desktop, monitor, local, station, selected_contact_site=None):
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
        return empty, {}, detected, None, selected_contact_site or station

    combined = pd.concat(frames, ignore_index=True, sort=False)

    # IMPORTANT: use the exact Contact Site selected by the user.
    combined, site_col, site_filter = filter_station(
        combined,
        station,
        selected_contact_site,
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
'''

if not pattern.search(text):
    raise SystemExit('Could not locate build_status_results(). No changes were made.')
text = pattern.sub(replacement, text, count=1)

# Make the Pre-Reconciliation summary use the exact Contact Site selector.
text = text.replace(
'''    "Desktop CMDB",
    power_station,
)''',
'''    "Desktop CMDB",
    power_station,
    selected_contact_site,
)''',
1,
)
text = text.replace(
'''    "Monitor CMDB",
    power_station,
)''',
'''    "Monitor CMDB",
    power_station,
    selected_contact_site,
)''',
1,
)

# Make the post-reconciliation status overview use the same exact Contact Site.
text = text.replace(
'build_status_results(desktop, monitor, local, power_station)',
'build_status_results(desktop, monitor, local, power_station, selected_contact_site)',
1,
)

# Validate Python syntax before overwriting the damaged app.
compile(text, str(APP), 'exec')
APP.write_text(text, encoding='utf-8')

print('SUCCESS: app.py syntax and status function repaired.')
print(f'Backup created: {BACKUP.name}')
print('Both status sections now use the selected Contact Site and the same VBA status logic.')
print('Next command: python -m py_compile app.py')
