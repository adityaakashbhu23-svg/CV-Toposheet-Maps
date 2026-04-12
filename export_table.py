# export_table.py  –  Export results as a formatted table
#
# Outputs the exact format:
#   Extracted Text | Grid Number | Map Number | Map Name | Year
#
# Usage:
#   python export_table.py                          → export ALL maps
#   python export_table.py "Palamau"                → filter by map name
#   python export_table.py "Palamau" settlement     → filter by map + type
#
# Output files:
#   results/table_export.csv    ← open in Excel
#   results/table_export.html   ← open in browser (nicely formatted)

import sys
import csv
import re
import sqlite3
from pathlib import Path

import config

DB_PATH   = config.RESULTS_FOLDER / 'toposheet.db'
OUT_CSV   = config.RESULTS_FOLDER / 'table_export.csv'
OUT_HTML  = config.RESULTS_FOLDER / 'table_export.html'


# ─────────────────────────────────────────────────────────────────────────────
def parse_map_name(raw: str):
    """
    Parse a raw map_name like '63 P]14 Palamau (1918) Preliminary (1)'
    Returns: (map_number, map_name, year)
    e.g.    ('63 P/14', 'Palamau', '1918')
    """
    # Extract year: last 4-digit number in parentheses
    year_match = re.search(r'\((\d{4})\)', raw)
    year = year_match.group(1) if year_match else ''

    # Extract map number: leading pattern like "63 P]14" or "72 D]04" or "72 H"
    num_match = re.match(r'^(\d+\s+[A-Z]+(?:[/\]]\d+)?)', raw.strip())
    if num_match:
        map_number = num_match.group(1).replace(']', '/').strip()
    else:
        map_number = ''

    # Extract clean map name: text between the number and the year
    clean = raw
    if num_match:
        clean = clean[num_match.end():].strip()
    # Remove year and anything in parens after it
    clean = re.sub(r'\s*\(\d{4}\).*$', '', clean).strip()
    # Remove trailing "Preliminary" variants
    clean = re.sub(r'\s*(Preliminary|District)\s*$', '', clean).strip()
    # If "District" was stripped, re-add it for proper place names
    # Actually keep "District" — it's part of the name
    clean = raw
    if num_match:
        clean = clean[num_match.end():].strip()
    clean = re.sub(r'\s*\(\d{4}\).*$', '', clean).strip()
    clean = re.sub(r'\s*\(\d+\)\s*$', '', clean).strip()  # remove trailing (1), (2)

    return map_number, clean, year


# ─────────────────────────────────────────────────────────────────────────────
def fetch_rows(map_filter: str = '', type_filter: str = '') -> list:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = """
        SELECT feature_name, grid_reference, map_name, feature_type, confidence
        FROM features
        WHERE 1=1
    """
    params = []
    if map_filter:
        query += " AND map_name LIKE ?"
        params.append(f'%{map_filter}%')
    if type_filter:
        query += " AND feature_type = ?"
        params.append(type_filter)
    query += " ORDER BY map_name, grid_reference, feature_name"

    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ─────────────────────────────────────────────────────────────────────────────
def export_csv(rows: list):
    with open(OUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:  # utf-8-sig for Excel
        writer = csv.writer(f)
        writer.writerow(['Extracted Text', 'Grid Number', 'Map Number', 'Map Name', 'Year', 'Feature Type', 'Confidence'])
        for r in rows:
            map_number, map_name, year = parse_map_name(r['map_name'])
            writer.writerow([
                r['feature_name'],
                r['grid_reference'],
                map_number,
                map_name,
                year,
                r['feature_type'],
                f"{r['confidence']:.2f}",
            ])
    print(f"[Export] CSV → {OUT_CSV}  ({len(rows)} rows)")


# ─────────────────────────────────────────────────────────────────────────────
def export_html(rows: list, map_filter: str = '', type_filter: str = ''):
    title = 'CV-Toposheet: Extracted Features'
    if type_filter:
        title += f' ({type_filter}s)'

    # ── map stats ──────────────────────────────────────────────────────────
    map_stats = {}
    for r in rows:
        key = r['map_name']
        if key not in map_stats:
            _, _, yr = parse_map_name(r['map_name'])
            map_stats[key] = {'year': yr, 'count': 0}
        map_stats[key]['count'] += 1
    sorted_keys = sorted(map_stats.keys())
    total_maps  = len(sorted_keys)

    # ── table rows (with data-* attrs for JS filtering / compare) ──────────
    table_rows_html = ''
    for r in rows:
        map_number, map_name, year = parse_map_name(r['map_name'])
        conf = float(r['confidence'])
        conf_color = '#2d6a2d' if conf >= 0.85 else ('#b8860b' if conf >= 0.70 else '#8b0000')
        mapkey    = r['map_name'].replace('"', '').replace("'", '')
        safe_name = r['feature_name'].replace('"', '').replace("'", '').replace('<', '').replace('>', '')
        table_rows_html += (
            f'<tr data-mk="{mapkey}" data-ft="{r["feature_type"]}" data-fn="{safe_name.lower()}">'
            f'<td><strong>{r["feature_name"]}</strong></td>'
            f'<td>{r["grid_reference"] or "-"}</td>'
            f'<td>{map_number}</td>'
            f'<td>{map_name}</td>'
            f'<td>{year}</td>'
            f'<td><span class="badge badge-{r["feature_type"]}">{r["feature_type"]}</span></td>'
            f'<td style="color:{conf_color}">{conf:.2f}</td>'
            f'</tr>\n'
        )

    # ── sidebar checkboxes ────────────────────────────────────────────────
    checkboxes_html = ''
    for k in sorted_keys:
        s      = map_stats[k]
        safe_k = k.replace('"', '').replace("'", '')
        meta   = f'{s["year"]} · {s["count"]}' if s['year'] else f'· {s["count"]}'
        checkboxes_html += (
            f'<label class="map-label">'
            f'<input type="checkbox" class="map-cb" value="{safe_k}" checked onchange="onToggle()">'
            f'<span class="ml-text">'
            f'<span class="ml-num" style="white-space:normal;word-break:break-word">{k}</span>'
            f'</span>'
            f'<span class="ml-meta">{meta}</span>'
            f'</label>\n'
        )

    # ── full HTML ─────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Segoe UI',Arial,sans-serif; background:#f0f2f5; color:#222; height:100vh; display:flex; flex-direction:column; overflow:hidden; }}

/* ── Header ── */
.hdr {{ background:#0E7490; color:white; padding:12px 22px; flex-shrink:0; display:flex; align-items:center; gap:16px; }}
.hdr-title {{ font-size:1.15em; font-weight:700; }}
.hdr-sub   {{ font-size:0.78em; color:rgba(255,255,255,0.72); margin-top:2px; }}
.hdr-stats {{ margin-left:auto; display:flex; gap:18px; }}
.hdr-stat .n {{ font-size:1.2em; font-weight:bold; color:#ffffff; text-align:center; }}
.hdr-stat .l {{ font-size:0.7em; color:rgba(255,255,255,0.65); text-align:center; }}

/* ── Layout ── */
.layout {{ display:flex; flex:1; overflow:hidden; }}

/* ── Sidebar ── */
.sidebar {{ width:240px; min-width:200px; background:white; border-right:1px solid #dde; display:flex; flex-direction:column; overflow:hidden; }}
.sb-head {{ padding:10px 12px; background:#f7f9fb; border-bottom:1px solid #eee; flex-shrink:0; }}
.sb-head h3 {{ font-size:0.75em; text-transform:uppercase; color:#888; letter-spacing:.06em; margin-bottom:7px; }}
.sb-acts {{ display:flex; gap:5px; }}
.sb-btn {{ font-size:0.73em; padding:3px 8px; border:1px solid #ccc; border-radius:3px; background:white; cursor:pointer; color:#555; }}
.sb-btn:hover {{ background:#eef; }}
.sb-maps {{ flex:1; overflow-y:auto; padding:7px; }}
.map-label {{ display:flex; align-items:center; gap:6px; padding:6px 7px; border-radius:5px; cursor:pointer; margin-bottom:3px; border:1px solid transparent; font-size:0.8em; transition:background .1s; }}
.map-label:hover {{ background:#f0f4ff; border-color:#d0d8f0; }}
.map-label input[type=checkbox] {{ width:14px; height:14px; accent-color:#2c3e50; flex-shrink:0; cursor:pointer; }}
.ml-text  {{ flex:1; overflow:hidden; }}
.ml-num   {{ font-weight:400; color:#2c3e50; display:block; font-size:0.88em; white-space:normal; word-break:break-word; }}
.ml-name  {{ color:#888; font-size:0.82em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; display:block; }}
.ml-meta  {{ font-size:0.72em; color:#bbb; flex-shrink:0; white-space:nowrap; }}
.sb-cmp {{ padding:9px 10px; border-top:1px solid #eee; flex-shrink:0; }}
.cmp-btn {{ width:100%; padding:9px; background:#c0392b; color:white; border:none; border-radius:5px; font-size:0.83em; font-weight:700; cursor:pointer; transition:background .15s; }}
.cmp-btn:hover:not(:disabled) {{ background:#a93226; }}
.cmp-btn:disabled {{ background:#ccc; cursor:not-allowed; color:#eee; }}
.cmp-hint {{ font-size:0.7em; color:#aaa; text-align:center; margin-top:4px; }}

/* ── Main view ── */
.main {{ flex:1; display:flex; flex-direction:column; overflow:hidden; }}
.toolbar {{ padding:9px 14px; background:white; border-bottom:1px solid #eee; display:flex; gap:8px; align-items:center; flex-wrap:wrap; flex-shrink:0; }}
input#search {{ padding:6px 11px; width:230px; border:1px solid #ccc; border-radius:4px; font-size:0.83em; }}
select.ft-sel {{ padding:6px 9px; border:1px solid #ccc; border-radius:4px; font-size:0.83em; background:white; }}
.row-badge {{ background:#2c3e50; color:white; padding:3px 9px; border-radius:10px; font-size:0.76em; }}
.clr-btn {{ padding:5px 11px; background:#95a5a6; color:white; border:none; border-radius:4px; font-size:0.76em; cursor:pointer; }}
.clr-btn:hover {{ background:#7f8c8d; }}
.dl-btn {{ padding:5px 11px; background:#27ae60; color:white; border:none; border-radius:4px; font-size:0.76em; cursor:pointer; font-weight:600; }}
.dl-btn:hover {{ background:#219a52; }}
.tbl-wrap {{ flex:1; overflow:auto; }}
table {{ border-collapse:collapse; width:100%; background:white; }}
th {{ background:#22D3EE; color:white; padding:9px 13px; text-align:left; font-size:0.8em; position:sticky; top:0; z-index:1; white-space:nowrap; }}
td {{ padding:6px 13px; border-bottom:1px solid #f0f0f0; font-size:0.82em; }}
tr:hover td {{ background:#f5f8ff; }}
.badge {{ padding:2px 7px; border-radius:10px; font-size:0.75em; font-weight:bold; color:white; }}
.badge-settlement {{ background:#6a1b9a; }}
.badge-river       {{ background:#1565c0; }}
.badge-mountain    {{ background:#8e44ad; }}
.badge-landmark    {{ background:#e67e22; }}
.badge-forest      {{ background:#1a7a4a; }}
.badge-road        {{ background:#7f8c8d; }}
.badge-lake        {{ background:#0288d1; }}
.badge-noise       {{ background:#bdc3c7; color:#555; }}
.no-rows {{ text-align:center; padding:40px; color:#bbb; }}

/* ── Compare Panel ── */
#cmpPanel {{ display:none; flex:1; flex-direction:column; overflow:hidden; }}
.cp-hdr {{ padding:12px 20px; background:#0E7490; color:white; display:flex; align-items:center; gap:12px; flex-shrink:0; }}
.cp-hdr h2 {{ font-size:0.98em; font-weight:600; }}
.cp-tag {{ font-size:0.77em; color:#8899bb; margin-top:2px; }}
.back-btn {{ margin-left:auto; padding:5px 13px; background:white; color:#0E7490; border:none; border-radius:4px; font-size:0.8em; font-weight:700; cursor:pointer; }}
.back-btn:hover {{ background:#e8ecf0; }}
.cp-summary {{ display:flex; gap:1px; flex-shrink:0; background:#dde; }}
.cp-stat {{ flex:1; background:white; padding:10px 14px; text-align:center; }}
.cp-stat .csn {{ font-size:1.4em; font-weight:bold; }}
.cp-stat .csl {{ font-size:0.72em; color:#888; }}
.cp-stat.common .csn {{ color:#27ae60; }}
.cp-stat.uniq   .csn {{ color:#e74c3c; }}
.cp-tabs {{ display:flex; border-bottom:2px solid #dde; background:white; flex-shrink:0; overflow-x:auto; }}
.cp-tab {{ padding:9px 16px; font-size:0.81em; cursor:pointer; border-bottom:3px solid transparent; margin-bottom:-2px; white-space:nowrap; color:#666; font-weight:500; }}
.cp-tab:hover {{ background:#f5f5f5; }}
.cp-tab.active {{ border-bottom-color:#c0392b; color:#c0392b; font-weight:700; }}
.cp-body {{ flex:1; overflow:auto; padding:14px; }}
.cp-sec {{ display:none; }}
.cp-sec.active {{ display:block; }}
.cp-sec h3 {{ font-size:0.85em; color:#555; margin-bottom:9px; font-weight:600; }}
.cp-tbl {{ border-collapse:collapse; width:100%; background:white; box-shadow:0 1px 3px rgba(0,0,0,.08); border-radius:5px; overflow:hidden; margin-bottom:16px; }}
.cp-tbl th {{ background:#34495e; color:white; padding:7px 11px; font-size:0.78em; text-align:left; }}
.cp-tbl td {{ padding:5px 11px; border-bottom:1px solid #f0f0f0; font-size:0.8em; }}
.cp-tbl tr:hover td {{ background:#f8f9fa; }}
.row-common {{ background:#edfbf3 !important; }}
.row-unique {{ background:#fff6ed !important; }}
.empty-msg {{ color:#aaa; font-style:italic; padding:18px; text-align:center; }}
</style>
</head>
<body>

<!-- Header -->
<div class="hdr">
  <div>
    <div class="hdr-title">CV-Toposheet / Maps</div>
    <div class="hdr-sub">Extracted Map Feature</div>
  </div>
  <div class="hdr-stats">
    <div class="hdr-stat"><div class="n">{len(rows):,}</div><div class="l">Features</div></div>
    <div class="hdr-stat"><div class="n">{total_maps}</div><div class="l">Maps</div></div>
  </div>
</div>

<div class="layout">

  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sb-head">
      <h3>Maps ({total_maps})</h3>
      <div class="sb-acts">
        <button class="sb-btn" onclick="setAll(true)">Select All</button>
        <button class="sb-btn" onclick="setAll(false)">Clear All</button>
      </div>
    </div>
    <div class="sb-maps">
{checkboxes_html}
    </div>
    <div class="sb-cmp">
      <button class="cmp-btn" id="cmpBtn" onclick="runCompare()" disabled>&#9878; Compare Selected</button>
      <div class="cmp-hint" id="cmpHint">Check 2+ maps to compare</div>
    </div>
  </div>

  <!-- Main table view -->
  <div class="main" id="mainView">
    <div class="toolbar">
      <input type="text" id="search" oninput="applyFilters()" placeholder="&#128269; Search text, grid, name...">
      <select class="ft-sel" id="typeFilter" onchange="applyFilters()">
        <option value="">All Feature Types</option>
        <option value="settlement">settlement</option>
        <option value="river">river</option>
        <option value="mountain">mountain</option>
        <option value="forest">forest</option>
        <option value="road">road</option>
        <option value="lake">lake</option>
        <option value="landmark">landmark</option>
        <option value="noise">noise</option>
      </select>
      <span class="row-badge" id="rowCnt">{len(rows):,} rows</span>
      <button class="clr-btn" onclick="clearFilters()">Clear</button>
      <button class="dl-btn" onclick="downloadCSV()">&#11015; Download Excel</button>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Extracted Text</th><th>Grid Number</th><th>Map Number</th>
            <th>Map Name</th><th>Year</th><th>Feature Type</th><th>Confidence</th>
          </tr>
        </thead>
        <tbody id="tBody">
{table_rows_html}
        </tbody>
      </table>
      <div class="no-rows" id="noRows" style="display:none">No matching features found.</div>
    </div>
  </div>

  <!-- Compare panel -->
  <div id="cmpPanel">
    <div class="cp-hdr">
      <div>
        <h2>Map Comparison</h2>
        <div class="cp-tag" id="cpTag"></div>
      </div>
      <button class="back-btn" onclick="closeCompare()">&#8592; Back to Table</button>
    </div>
    <div class="cp-summary" id="cpSummary"></div>
    <div class="cp-tabs"    id="cpTabs"></div>
    <div class="cp-body"    id="cpBody"></div>
  </div>

</div><!-- layout -->

<script>
var allRows = Array.from(document.querySelectorAll('#tBody tr'));

/* ── Filters ──────────────────────────────────────────────────────── */
function getChecked() {{
  return Array.from(document.querySelectorAll('.map-cb:checked')).map(cb => cb.value);
}}

function applyFilters() {{
  var search  = (document.getElementById('search').value || '').toLowerCase().trim();
  var typeVal = document.getElementById('typeFilter').value;
  var checked = new Set(getChecked());
  var vis = 0;
  allRows.forEach(function(row) {{
    var show = checked.has(row.dataset.mk);
    if (show && typeVal  && row.dataset.ft !== typeVal) show = false;
    if (show && search   && !row.innerText.toLowerCase().includes(search)) show = false;
    row.style.display = show ? '' : 'none';
    if (show) vis++;
  }});
  document.getElementById('rowCnt').textContent = vis.toLocaleString() + ' rows';
  document.getElementById('noRows').style.display = vis === 0 ? '' : 'none';
}}

function onToggle() {{
  var n = getChecked().length;
  var btn = document.getElementById('cmpBtn');
  var hint = document.getElementById('cmpHint');
  btn.disabled = n < 2;
  hint.textContent = n < 2 ? 'Check 2+ maps to compare' : n + ' maps selected \u2014 click to compare';
  applyFilters();
}}

function setAll(v) {{
  document.querySelectorAll('.map-cb').forEach(function(cb) {{ cb.checked = v; }});
  onToggle();
}}

function clearFilters() {{
  document.getElementById('search').value = '';
  document.getElementById('typeFilter').value = '';
  applyFilters();
}}

function downloadCSV() {{
  var vis = allRows.filter(function(r) {{ return r.style.display !== 'none'; }});
  var lines = ['\uFEFF"Extracted Text","Grid Number","Map Number","Map Name","Year","Feature Type","Confidence"'];
  vis.forEach(function(row) {{
    var c = row.querySelectorAll('td');
    var cols = Array.from(c).map(function(td) {{ return '"' + td.textContent.trim().replace(/"/g, '""') + '"'; }});
    lines.push(cols.join(','));
  }});
  var blob = new Blob([lines.join('\\n')], {{type:'text/csv;charset=utf-8;'}});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a'); a.href = url; a.download = 'toposheet_features.csv'; a.click();
  URL.revokeObjectURL(url);
}}

/* ── Compare ──────────────────────────────────────────────────────── */
function esc(s) {{
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function runCompare() {{
  var maps = getChecked();
  if (maps.length < 2) return;

  /* build per-map data */
  var data = {{}};
  maps.forEach(function(mk) {{ data[mk] = {{ names: new Set(), rows: [] }}; }});
  allRows.forEach(function(row) {{
    var mk = row.dataset.mk;
    if (data[mk]) {{
      var fn = (row.dataset.fn || '').trim();
      if (fn) data[mk].names.add(fn);
      data[mk].rows.push(row);
    }}
  }});

  /* common = name in ALL maps */
  var common = null;
  maps.forEach(function(mk) {{
    if (common === null) {{ common = new Set(data[mk].names); }}
    else {{ common = new Set([...common].filter(x => data[mk].names.has(x))); }}
  }});

  /* unique per map */
  var unique = {{}};
  maps.forEach(function(mk) {{
    unique[mk] = new Set([...data[mk].names].filter(x => !common.has(x)));
  }});

  /* ── summary bar ── */
  var sumHTML = '<div class="cp-stat common"><div class="csn">' + common.size + '</div><div class="csl">Common Features</div></div>';
  maps.forEach(function(mk) {{
    sumHTML += '<div class="cp-stat uniq"><div class="csn">' + unique[mk].size + '</div><div class="csl">Only in ' + esc(mk) + '</div></div>';
  }});
  document.getElementById('cpSummary').innerHTML = sumHTML;

  /* ── tabs ── */
  var tabsHTML = '<div class="cp-tab active" data-sec="sec-common">&#10003; Common (' + common.size + ')</div>';
  maps.forEach(function(mk) {{
    var sid = 'sec-u-' + mk.replace(/[^a-z0-9]/gi, '-');
    tabsHTML += '<div class="cp-tab" data-sec="' + sid + '">Only: ' + esc(mk) + ' (' + unique[mk].size + ')</div>';
  }});
  document.getElementById('cpTabs').innerHTML = tabsHTML;
  document.getElementById('cpTabs').querySelectorAll('.cp-tab').forEach(function(tab) {{
    tab.addEventListener('click', function() {{ showTab(this, this.dataset.sec); }});
  }});

  /* ── sections ── */
  var bodyHTML = '';

  /* Common section */
  bodyHTML += '<div class="cp-sec active" id="sec-common">';
  bodyHTML += '<h3>Features found in ALL ' + maps.length + ' selected maps \u2014 ' + common.size + ' common feature(s)</h3>';
  if (common.size === 0) {{
    bodyHTML += '<div class="empty-msg">No features with identical names across all selected maps.</div>';
  }} else {{
    bodyHTML += '<table class="cp-tbl"><thead><tr><th>Feature Name</th><th>Present in Maps</th></tr></thead><tbody>';
    [...common].sort().forEach(function(fn) {{
      var mapList = maps.filter(mk => data[mk].names.has(fn)).join(', ');
      bodyHTML += '<tr class="row-common"><td><strong>' + esc(fn) + '</strong></td><td>' + esc(mapList) + '</td></tr>';
    }});
    bodyHTML += '</tbody></table>';
  }}
  bodyHTML += '</div>';

  /* Unique-per-map sections */
  maps.forEach(function(mk) {{
    var sid = 'sec-u-' + mk.replace(/[^a-z0-9]/gi, '-');
    bodyHTML += '<div class="cp-sec" id="' + sid + '">';
    bodyHTML += '<h3>Features found ONLY in <em>' + esc(mk) + '</em> \u2014 not in other selected maps (' + unique[mk].size + ')</h3>';
    if (unique[mk].size === 0) {{
      bodyHTML += '<div class="empty-msg">Every feature in this map also appears in the other selected maps.</div>';
    }} else {{
      bodyHTML += '<table class="cp-tbl"><thead><tr><th>Feature Name</th><th>Grid</th><th>Type</th><th>Confidence</th></tr></thead><tbody>';
      data[mk].rows.forEach(function(row) {{
        if (!unique[mk].has((row.dataset.fn||'').trim())) return;
        var c = row.querySelectorAll('td');
        var name = c[0] ? c[0].textContent.trim() : '';
        var grid = c[1] ? c[1].textContent.trim() : '';
        var type = c[5] ? c[5].textContent.trim() : '';
        var conf = c[6] ? c[6].textContent.trim() : '';
        bodyHTML += '<tr class="row-unique"><td><strong>' + esc(name) + '</strong></td><td>' + esc(grid) + '</td><td>' + esc(type) + '</td><td>' + esc(conf) + '</td></tr>';
      }});
      bodyHTML += '</tbody></table>';
    }}
    bodyHTML += '</div>';
  }});

  document.getElementById('cpBody').innerHTML = bodyHTML;
  document.getElementById('cpTag').textContent = 'Comparing: ' + maps.join(' \u2014 ');

  document.getElementById('mainView').style.display = 'none';
  document.getElementById('cmpPanel').style.display = 'flex';
}}

function closeCompare() {{
  document.getElementById('cmpPanel').style.display = 'none';
  document.getElementById('mainView').style.display = 'flex';
}}

function showTab(el, secId) {{
  document.querySelectorAll('.cp-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.querySelectorAll('.cp-sec').forEach(s => s.classList.remove('active'));
  var sec = document.getElementById(secId);
  if (sec) sec.classList.add('active');
}}

/* init */
onToggle();
</script>
</body>
</html>"""

    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"[Export] HTML → {OUT_HTML}  (open in browser)")


# ─────────────────────────────────────────────────────────────────────────────
def main():
    map_filter  = sys.argv[1] if len(sys.argv) > 1 else ''
    type_filter = sys.argv[2] if len(sys.argv) > 2 else ''

    print(f"[Export] Querying database...")
    if map_filter:
        print(f"         Map filter  : '{map_filter}'")
    if type_filter:
        print(f"         Type filter : '{type_filter}'")

    rows = fetch_rows(map_filter, type_filter)

    if not rows:
        print("[Export] No results found.")
        return

    print(f"[Export] Found {len(rows)} features\n")
    export_csv(rows)
    export_html(rows, map_filter, type_filter)

    print()
    print("  Open results/table_export.csv   in Excel")
    print("  Open results/table_export.html  in browser (searchable table)")


if __name__ == '__main__':
    main()
