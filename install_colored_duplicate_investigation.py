from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_colored_duplicate_investigation.py')
MARKER = '# COLORED_DUPLICATE_INVESTIGATION_V1'

if not APP.exists():
    raise SystemExit('app.py not found. Put this installer in KomatiAssetApp next to app.py.')

text = APP.read_text(encoding='utf-8')
if MARKER in text:
    raise SystemExit('The coloured duplicate investigation is already installed.')

shutil.copy2(APP, BACKUP)

# Add helper functions before filter_station.
anchor = '\ndef filter_station('
if anchor not in text:
    raise SystemExit('Could not find filter_station(). No changes made.')

helpers = r'''

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

'''
text = text.replace(anchor, helpers + anchor, 1)

# Replace duplicate calculations; support either old or departmental variable assignment.
patterns = [
    ('duplicate_cmdb = duplicate_identifier_summary(cmdb, "CMDB")',
     'duplicate_cmdb, cmdb_duplicate_evidence = build_duplicate_investigation(cmdb, "CMDB")'),
    ('duplicate_local = duplicate_identifier_summary(local, "Local Register")',
     'duplicate_local, local_duplicate_evidence = build_duplicate_investigation(local, "Local Register")'),
    ('duplicate_cmdb = duplicate_rows(cmdb)',
     'duplicate_cmdb, cmdb_duplicate_evidence = build_duplicate_investigation(cmdb, "CMDB")'),
    ('duplicate_local = duplicate_rows(local)',
     'duplicate_local, local_duplicate_evidence = build_duplicate_investigation(local, "Local Register")'),
]
for old, new in patterns:
    text = text.replace(old, new, 1)

if 'cmdb_duplicate_evidence = build_duplicate_investigation' not in text:
    raise SystemExit('Could not replace duplicate calculations. No changes made.')

# Introduce a coloured investigation section before Detailed reconciliation results.
details_anchor = 'st.subheader("Detailed reconciliation results")'
if details_anchor not in text:
    raise SystemExit('Could not find Detailed reconciliation results. No changes made.')

section = r'''st.markdown("## 🔍 Duplicate Comparison Evidence")
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

st.subheader("Detailed reconciliation results")'''
text = text.replace(details_anchor, section, 1)

# Keep downloadable report clean by excluding hidden Issue Key.
text = text.replace(
    '"CMDB Duplicates": duplicate_cmdb,',
    '"CMDB Duplicate Identifiers": duplicate_cmdb.drop(columns=["Issue Key"], errors="ignore"),',
)
text = text.replace(
    '"Local Duplicates": duplicate_local,',
    '"Local Duplicate Identifiers": duplicate_local.drop(columns=["Issue Key"], errors="ignore"),',
)

compile(text, str(APP), 'exec')
APP.write_text(text, encoding='utf-8')

print('SUCCESS: coloured duplicate comparison evidence installed.')
print(f'Backup created: {BACKUP.name}')
print('The app now shows identifier, source-record count, reason, colour risk, and compared records.')
print('Next: python -m py_compile app.py')
