from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_priority_cmdb_fix.py')

if not APP.exists():
    raise SystemExit('app.py not found. Put this file next to app.py in KomatiAssetApp.')

text = APP.read_text(encoding='utf-8')
shutil.copy2(APP, BACKUP)

# Remove the misleading Active Asset Reconciliation block introduced earlier.
start = text.find('# Active Asset Reconciliation requested during the OU IM Departmental Meeting.')
end_marker = '# -----------------------------------------------------------------------------\n# EXECUTIVE DASHBOARD'
end = text.find(end_marker)

if start == -1:
    # Try locating by the visible heading if the comment moved.
    heading_pos = text.find('st.markdown("## 🟢 Active Asset Reconciliation")')
    if heading_pos != -1:
        start = text.rfind('\n', 0, heading_pos) + 1

if start == -1 or end == -1 or start >= end:
    raise SystemExit('Could not safely locate the Active Asset Reconciliation block. No changes made.')

priority_section = '''# Priority CMDB Status requested during the OU IM Departmental Meeting.
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

'''

text = text[:start] + priority_section + text[end:]

# Validate before overwriting.
compile(text, str(APP), 'exec')
APP.write_text(text, encoding='utf-8')

print('SUCCESS: misleading Active Asset Reconciliation replaced.')
print(f'Backup created: {BACKUP.name}')
print('Priority view now highlights In Service + Preproduction while retaining In Store, Loan and Retired.')
print('Local Asset reconciliation remains separate and unchanged.')
print('Next: python -m py_compile app.py')
