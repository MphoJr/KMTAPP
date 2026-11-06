from pathlib import Path
import shutil

APP = Path(__file__).with_name("app.py")
BACKUP = Path(__file__).with_name("app_before_desktop_loan_rule.py")

if not APP.exists():
    raise SystemExit(
        "app.py was not found. Put this file in the KomatiAssetApp folder next to app.py."
    )

text = APP.read_text(encoding="utf-8")

rule_marker = "# KOMATI_DESKTOP_LOAN_RULE_V1"
if rule_marker in text:
    raise SystemExit("The Komati Desktop Loan rule is already installed. No changes were made.")

anchor = '''    result["Status Group"] = result["CMDB Status Key"].map(map_status)
    result["CMDB Source"] = source_name
'''

replacement = '''    result["Status Group"] = result["CMDB Status Key"].map(map_status)

    # KOMATI_DESKTOP_LOAN_RULE_V1
    # Approved reporting rule for the Komati Desktop CMDB:
    # - Count Loan Unit records under Cost Centre 117056 as Loan.
    # - Report the remaining Komati Loan Unit records as In Store.
    # This changes Desktop Loan from 14 to 3 and In Store from 299 to 310.
    if source_name == "Desktop CMDB" and station == "Komati":
        cost_centre_column = find_column(
            result.columns,
            ["Cost Centre", "Cost Center"],
        )

        if cost_centre_column:
            cost_centre_key = (
                result[cost_centre_column]
                .fillna("")
                .astype(str)
                .str.strip()
                .str.replace(r"\\.0$", "", regex=True)
            )

            raw_loan_mask = result["CMDB Status Key"].str.contains(
                "loan",
                case=False,
                na=False,
            )

            approved_loan_mask = raw_loan_mask & cost_centre_key.eq("117056")
            store_loan_mask = raw_loan_mask & ~cost_centre_key.eq("117056")

            result.loc[approved_loan_mask, "Status Group"] = "Loan"
            result.loc[store_loan_mask, "Status Group"] = "In Store"

            result["Status Reporting Rule"] = "Standard status mapping"
            result.loc[
                approved_loan_mask,
                "Status Reporting Rule",
            ] = "Loan Unit retained as Loan: Cost Centre 117056"
            result.loc[
                store_loan_mask,
                "Status Reporting Rule",
            ] = "Loan Unit reported as In Store: Cost Centre is not 117056"

    result["CMDB Source"] = source_name
'''

if anchor not in text:
    raise SystemExit(
        "Could not find the status-mapping section in app.py. No changes were made."
    )

updated = text.replace(anchor, replacement, 1)
compile(updated, str(APP), "exec")

shutil.copy2(APP, BACKUP)
APP.write_text(updated, encoding="utf-8")

print("Komati Desktop Loan reporting rule installed successfully.")
print(f"Backup created: {BACKUP.name}")
print("Expected Desktop results: Loan = 3; In Store = 310.")
print("The raw CMDB Device Status remains visible for audit purposes.")
