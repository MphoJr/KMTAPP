from pathlib import Path
import re
import shutil

APP = Path(__file__).with_name('app.py')
BACKUP = Path(__file__).with_name('app_before_multiple_contact_sites.py')
MARKER = '# MULTIPLE_CONTACT_SITES_V1'

if not APP.exists():
    raise SystemExit('app.py not found. Put this installer next to app.py in KomatiAssetApp.')

text = APP.read_text(encoding='utf-8')
if MARKER in text:
    raise SystemExit('Multiple Contact Site selection is already installed.')

shutil.copy2(APP, BACKUP)

# 1) Change the UI from a single selectbox to a multiselect.
ui_pattern = re.compile(
    r'selected_contact_site\s*=\s*st\.selectbox\(\s*"CMDB Contact Site",\s*_all_sites,\s*index=_default_index,\s*key="selected_contact_site",\s*\)\s*\n\s*st\.info\(f"CMDB records will be filtered using the exact Contact Site: \{selected_contact_site\}"\)',
    re.S,
)

ui_replacement = '''# MULTIPLE_CONTACT_SITES_V1
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
)'''

if not ui_pattern.search(text):
    raise SystemExit('Could not locate the current Contact Site selectbox. No changes were made.')
text = ui_pattern.sub(ui_replacement, text, count=1)

# 2) Upgrade filter_station so it accepts one or many exact sites.
filter_pattern = re.compile(
    r'def filter_station\(df, station, selected_contact_site=None\):.*?(?=\ndef )',
    re.S,
)

filter_replacement = '''def filter_station(df, station, selected_contact_site=None):
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

'''

if not filter_pattern.search(text):
    raise SystemExit('Could not locate filter_station(). No changes were made.')
text = filter_pattern.sub(filter_replacement, text, count=1)

# 3) Replace downstream variable references with the selected list.
text = re.sub(r'\bselected_contact_site\b', 'selected_contact_sites', text)

# Restore function parameter names where needed; those functions may still use the singular parameter internally.
text = text.replace('def filter_station(df, station, selected_contact_sites=None):',
                    'def filter_station(df, station, selected_contact_site=None):')
text = text.replace('def build_precomparison_status_summary(frame, source_name, station, selected_contact_sites=None):',
                    'def build_precomparison_status_summary(frame, source_name, station, selected_contact_site=None):')
# Inside these functions, retain their parameter variable.
# Fix accidental references created by global substitution within the function bodies.
start = text.find('def filter_station(')
next_def = text.find('\ndef ', start + 1)
block = text[start:next_def]
block = block.replace('selected_contact_sites', 'selected_contact_site')
text = text[:start] + block + text[next_def:]

pre_start = text.find('def build_precomparison_status_summary(')
if pre_start != -1:
    pre_next = text.find('\ndef ', pre_start + 1)
    pre_block = text[pre_start:pre_next]
    pre_block = pre_block.replace('selected_contact_sites', 'selected_contact_site')
    text = text[:pre_start] + pre_block + text[pre_next:]

# 4) Make display/audit/export strings readable instead of showing Python list notation.
text = text.replace(
    'f"CMDB Contact Site: {selected_contact_sites}"',
    'f"CMDB Contact Sites: {\', \'.join(selected_contact_sites)}"',
)
text = text.replace(
    '"Contact Site": selected_contact_sites,',
    '"Contact Site": ", ".join(selected_contact_sites),',
)
text = text.replace(
    '("Contact Site", selected_contact_sites),',
    '("Contact Site", ", ".join(selected_contact_sites)),',
)

# String signatures should include the selected list deterministically.
text = text.replace('str(selected_contact_sites)', '"|".join(sorted(selected_contact_sites))')

# Validate syntax before modifying the working app.
compile(text, str(APP), 'exec')
APP.write_text(text, encoding='utf-8')

print('SUCCESS: Multiple Contact Site selection installed.')
print(f'Backup created: {BACKUP.name}')
print('Users can now select one or more exact CMDB Contact Sites.')
print('All selected sites are combined into one station population for status counts, reconciliation and the Asset Template.')
print('Next: python -m py_compile app.py')
