
const MODEL_INFO = {
  best:     '<b>Best Quality:</b> Parallel GCV OCR tiles + Vertex AI Gemini 2.5 Flash. Largest batches (150 items), fastest Gemini model. Requires GCP service account.',
  fast:     '<b>Groq LLaMA (Fast):</b> Parallel GCV OCR + Groq LLaMA 3.1 8B. No inter-batch delays. Fastest total processing time. Free Groq API key needed.',
  ensemble: '<b>Ensemble (All LLMs):</b> Parallel GCV OCR + ALL your configured LLMs run simultaneously. Results merged by majority vote for maximum confidence. Slowest but most accurate.',
  openai:   '<b>OpenAI GPT-4o:</b> Parallel GCV OCR + GPT-4o-mini. 80 items/batch. Reliable paid OpenAI API.',
  claude:   '<b>Claude Haiku:</b> Parallel GCV OCR + Claude 3.5 Haiku. Strong reasoning. Anthropic API key required.',
  grok:     '<b>Grok (xAI):</b> Parallel GCV OCR + Grok-3-mini. Fast xAI inference. xAI API key required.',
  gemini:   '<b>Gemini API:</b> Google Cloud Vision OCR + Gemini (API key). Uses GEMINI_API_KEY from .env no Vertex AI or GCP billing needed.',
  offline:  '<b>Offline OCR:</b> EasyOCR runs locally on your machine. No Google Cloud Vision or GCP service account needed. Internet required for LLM cleaning. Use this mode when you have no GCP credentials.',
};

function killRunningJob() {
  const btn = document.getElementById('killBannerBtn');
  btn.disabled = true;
  btn.style.background = '#b91c1c';
  btn.textContent = '⏳ Stopping…';
  fetch('/restart', {method:'POST'})
    .finally(() => {
      btn.textContent = '✔ Done — going home…';
      setTimeout(() => { window.location.href = '/'; }, 800);
    });
}
function selectCard(el) {
  document.querySelectorAll('.model-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  const val = el.querySelector('input[type=radio]').value;
  document.getElementById('modelInput').value = val;
  const infoEl = document.getElementById('modelInfo');
  if (infoEl && MODEL_INFO[val]) {
    const counts = infoEl.querySelector('span');
    infoEl.innerHTML = (MODEL_INFO[val] || '') + (counts ? '<br>' + counts.outerHTML : '');
  }
}

const fileInput = document.getElementById('fileInput');
const dropZone  = document.getElementById('dropZone');
const preview   = document.getElementById('preview');
const processBtn = document.getElementById('processBtn');

fileInput.addEventListener('change', showPreviews);
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  // Merge dropped files into _fileList (don't replace — accumulate)
  Array.from(e.dataTransfer.files).forEach(f => {
    const already = _fileList.some(x => x.name === f.name && x.size === f.size);
    if (!already) _fileList.push(f);
  });
  _syncInputFiles();
  _renderPreviews();
});

// DataTransfer-based file list so we can remove individual files
let _fileList = [];   // array of File objects

function _syncInputFiles() {
  const dt = new DataTransfer();
  _fileList.forEach(f => dt.items.add(f));
  fileInput.files = dt.files;
}

function _renderPreviews() {
  preview.innerHTML = '';
  if (!_fileList.length) {
    processBtn.disabled = true;
    processBtn.querySelector('.btn-sub').textContent = 'Select a map file to continue';
    return;
  }
  processBtn.disabled = false;
  processBtn.querySelector('.btn-sub').textContent = _fileList.length + ' file(s) ready';
  _fileList.forEach((f, idx) => {
    const item = document.createElement('div');
    item.className = 'prev-item';

    // Remove (×) button
    const rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'prev-remove';
    rm.title = 'Remove ' + f.name;
    rm.textContent = '\u00D7';
    rm.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _fileList.splice(idx, 1);
      _syncInputFiles();
      _renderPreviews();
    });
    rm.addEventListener('mouseenter', e => e.stopPropagation());
    rm.addEventListener('mouseover', e => e.stopPropagation());

    const img = document.createElement('img');
    img.className = 'prev-thumb';
    img.src = URL.createObjectURL(f);

    const name = document.createElement('div');
    name.className = 'prev-name';
    name.textContent = f.name;

    item.appendChild(rm);
    item.appendChild(img);
    item.appendChild(name);
    preview.appendChild(item);
  });
}

function showPreviews() {
  // Merge newly picked files into _fileList (don't replace — accumulate)
  const incoming = Array.from(fileInput.files);
  incoming.forEach(f => {
    const already = _fileList.some(x => x.name === f.name && x.size === f.size);
    if (!already) _fileList.push(f);
  });
  _syncInputFiles();
  _renderPreviews();
}

// Settings modal
const SECTION_IDS = ['llm','google','vertex','models','custom'];

// ── PIN Lock ──────────────────────────────────────────────────────────────
const PIN_KEY = 'cvt_settings_pin';
function _getPin()   { return localStorage.getItem(PIN_KEY) || ''; }
function _savePin(p) { localStorage.setItem(PIN_KEY, p); }
function _clearPin() { localStorage.removeItem(PIN_KEY); }

function _loadSettingsData() {
  fetch('/get_env').then(r => r.json()).then(data => {
    for (const [k, v] of Object.entries(data)) {
      const inp = document.getElementById('inp_' + k);
      if (inp) inp.value = v || '';
    }
  }).catch(() => {});
  document.getElementById('settingsModal').classList.add('open');
  _updateLockBtn();
}

function openSettings() {
  if (_getPin()) { _showPinEntry(); }
  else           { _loadSettingsData(); }
}

function closeSettings() {
  document.getElementById('settingsModal').classList.remove('open');
}
function openHelp() {
  document.getElementById('helpOverlay').classList.add('open');
}
function closeHelp() {
  document.getElementById('helpOverlay').classList.remove('open');
}

const REPORT_GITHUB_NEW_ISSUE_URL = 'https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps/issues/new';

function buildQuickGithubIssueUrl() {
  const model = getReportModelValue() || 'not provided';
  const title = encodeURIComponent('AI content report (quick)');
  const body = encodeURIComponent(
    'Category: AI content concern\\n' +
    'Model: ' + model + '\\n\\n' +
    'Please describe the issue here.\\n'
  );
  return REPORT_GITHUB_NEW_ISSUE_URL + '?title=' + title + '&body=' + body;
}

function updateQuickGithubReportLink() {
  const issueLink = document.getElementById('reportIssueLink');
  if (!issueLink) return;
  issueLink.href = buildQuickGithubIssueUrl();
  issueLink.textContent = 'Report in GitHub';
}

function openReportModal(evt) {
  try {
    if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
    const model = document.getElementById('modelInput');
    const selectedNameEl = document.querySelector('.model-card.selected .mc-name');
    const selectedName = selectedNameEl ? selectedNameEl.textContent.replace(/\\s+/g, ' ').trim() : '';
    const selectedCode = model ? model.value : '';
    const modelSelect = document.getElementById('report_model');
    const modelOther = document.getElementById('report_model_other');
    const known = ['best','fast','ensemble','openai','claude','grok','gemini','offline'];
    window._lastReportPayload = null;
    if (modelSelect) {
      if (known.includes(selectedCode)) {
        modelSelect.value = selectedCode;
        if (modelOther) modelOther.value = '';
      } else {
        modelSelect.value = 'other';
        if (modelOther) modelOther.value = selectedName || selectedCode;
      }
    }
    toggleReportModelOther();
    const catEl = document.getElementById('report_category');
    if (catEl) catEl.value = 'inappropriate-output';
    const snipEl = document.getElementById('report_snippet');
    if (snipEl) snipEl.value = '';
    const detEl = document.getElementById('report_details');
    if (detEl) detEl.value = '';
    const conEl = document.getElementById('report_contact');
    if (conEl) conEl.value = '';
    const statusEl = document.getElementById('reportStatus');
    if (statusEl) { statusEl.className = 'report-status'; statusEl.style.display = 'none'; statusEl.innerHTML = ''; }
    updateQuickGithubReportLink();
    const modal = document.getElementById('reportModal');
    if (modal) {
      modal.classList.add('open');
    } else {
      alert('Report modal not found on this page. Please go to the main page to report AI content.');
    }
  } catch (err) {
    var errBanner = document.getElementById('_reportErrBanner');
    if (!errBanner) {
      errBanner = document.createElement('div');
      errBanner.id = '_reportErrBanner';
      errBanner.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);background:#dc2626;color:#fff;padding:12px 24px;border-radius:8px;z-index:9999;font-weight:bold;box-shadow:0 4px 12px rgba(0,0,0,.4);';
      document.body.appendChild(errBanner);
    }
    errBanner.textContent = 'Report button error: ' + err.message;
    errBanner.style.display = 'block';
    setTimeout(function(){ errBanner.style.display = 'none'; }, 8000);
  }
}

function closeReportModal() {
  document.getElementById('reportModal').classList.remove('open');
}

function toggleReportModelOther() {
  const select = document.getElementById('report_model');
  const row = document.getElementById('report_model_other_row');
  if (!select || !row) return;
  row.style.display = select.value === 'other' ? 'flex' : 'none';
  updateQuickGithubReportLink();
}

function getReportModelValue() {
  const select = document.getElementById('report_model');
  const other = document.getElementById('report_model_other');
  if (!select) return '';
  if (select.value === 'other') {
    return (other && other.value ? other.value.trim() : 'Other');
  }
  const opt = select.options[select.selectedIndex];
  return opt ? opt.text : select.value;
}

function openEmailFallback(emailUrl) {
  if (!emailUrl) return;
  try {
    // Primary path for desktop browser/webview mailto handling.
    window.location.href = emailUrl;
    // Secondary path for environments that ignore location mailto navigation.
    setTimeout(() => {
      try {
        const a = document.createElement('a');
        a.href = emailUrl;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } catch (_) {}
    }, 50);
  } catch (_) {}
}

function submitAIReport() {
  const status = document.getElementById('reportStatus');
  const submitBtn = document.getElementById('reportSubmitBtn');
  const payload = {
    category: document.getElementById('report_category').value,
    model: getReportModelValue(),
    snippet: document.getElementById('report_snippet').value.trim(),
    details: document.getElementById('report_details').value.trim(),
    contact: document.getElementById('report_contact').value.trim(),
  };
  if (document.getElementById('report_model').value === 'other' && !payload.model) {
    status.className = 'report-status err';
    status.style.display = 'block';
    status.textContent = 'Please type the model name for Other.';
    return;
  }
  if (!payload.details) {
    status.className = 'report-status err';
    status.style.display = 'block';
    status.textContent = 'Please describe the issue before submitting.';
    return;
  }
  submitBtn.disabled = true;
  submitBtn.textContent = 'Submitting...';
  fetch('/report_ai_content', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  })
    .then(async r => {
      const raw = await r.text();
      let data = null;
      if (raw) {
        try {
          data = JSON.parse(raw);
        } catch (_err) {
          data = null;
        }
      }
      return { ok: r.ok, status: r.status, data, raw };
    })
    .then(({ ok, status: httpStatus, data, raw }) => {
      if (!ok || !data || !data.ok) {
        const msg = (data && data.error)
          || (raw && raw.trim())
          || ('Submission failed (HTTP ' + httpStatus + ').');
        throw new Error(msg);
      }
      window._lastReportPayload = data;
      status.className = 'report-status ok';
      status.style.display = 'block';
      status.innerHTML = 'Saved with reference <b>' + data.report_id + '</b>.';
      const actions = document.getElementById('reportActions');
      actions.classList.add('open');
      document.getElementById('reportIssueLink').href = data.issue_url;
      document.getElementById('reportIssueLink').textContent = 'Report in GitHub';
      openEmailFallback(data.email_url);
    })
    .catch(err => {
      status.className = 'report-status err';
      status.style.display = 'block';
      status.textContent = err.message || 'Submission failed.';
    })
    .finally(() => {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit';
    });
}

function copyAIReport() {
  const status = document.getElementById('reportStatus');
  const data = window._lastReportPayload;
  if (!data || !data.report_text) {
    status.className = 'report-status err';
    status.style.display = 'block';
    status.textContent = 'Submit a report first, then copy.';
    return;
  }
  navigator.clipboard.writeText(data.report_text)
    .then(() => {
      status.className = 'report-status ok';
      status.style.display = 'block';
      status.textContent = 'Report copied. You can paste it into email or support chat.';
    })
    .catch(() => {
      status.className = 'report-status err';
      status.style.display = 'block';
      status.textContent = 'Copy failed. Please use the email or GitHub link.';
    });
}

function saveSettings() {
  const payload = {};
  document.querySelectorAll('.key-row-input input').forEach(inp => {
    const key = inp.id.replace('inp_', '');
    payload[key] = inp.value.trim();
  });
  fetch('/save_env', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(() => {
    const notice = document.getElementById('saveNotice');
    notice.style.display = 'flex';
    setTimeout(() => { notice.style.display = 'none'; closeSettings(); }, 1200);
  });
}

function toggleEye(key) {
  const inp = document.getElementById('inp_' + key);
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

function uploadJsonFile(inputId, targetName, statusId) {
  const fileInput = document.getElementById(inputId);
  const statusEl = document.getElementById(statusId);
  if (!fileInput.files.length) { statusEl.style.color='#c00'; statusEl.textContent='No file selected.'; return; }
  const fd = new FormData();
  fd.append('json_file', fileInput.files[0]);
  fd.append('target', targetName);
  statusEl.style.color='#888'; statusEl.textContent='Uploading...';
  fetch('/upload_service_account', { method:'POST', body:fd })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { statusEl.style.color='#16a34a'; statusEl.textContent='\u2713 ' + fileInput.files[0].name + ' uploaded successfully'; }
      else      { statusEl.style.color='#c00';     statusEl.textContent='Error: ' + (d.error||'failed'); }
    }).catch(() => { statusEl.style.color='#c00'; statusEl.textContent='Upload failed.'; });
}

function addCustomKeyRow() {
  const name = document.getElementById('custom_key_name').value.trim().toUpperCase().replace(/[^A-Z0-9_]/g,'');
  const val  = document.getElementById('custom_key_value').value.trim();
  if (!name) { document.getElementById('custom_key_name').focus(); return; }
  // Avoid duplicates
  if (document.getElementById('inp_' + name)) { alert('Key "' + name + '" already exists above.'); return; }
  const container = document.getElementById('custom_key_rows');
  const row = document.createElement('div'); row.className = 'key-row'; row.id = 'custom_row_' + name;
  row.innerHTML = `<label style="font-size:0.82em;min-width:160px;">${name}</label>
    <div class="key-row-input">
      <input type="password" id="inp_${name}" class="key-row-input" value="${val.replace(/"/g,'&quot;')}" style="flex:1;border:1px solid #cdd;border-radius:5px;padding:5px 8px;font-size:0.82em;">
      <button class="eye-btn" onclick="toggleEye('${name}')">&#128065;</button>
      <button class="eye-btn" style="color:#c00;" onclick="document.getElementById('custom_row_${name}').remove()" title="Remove">&#10005;</button>
    </div>`;
  container.appendChild(row);
  document.getElementById('custom_key_name').value = '';
  document.getElementById('custom_key_value').value = '';
  document.getElementById('custom_key_name').focus();
}

function toggleSection(id) {
  document.getElementById('key_section_' + id).classList.toggle('collapsed');
}

function showAllSections() {
  SECTION_IDS.forEach(id => {
    document.getElementById('key_section_' + id).classList.remove('collapsed');
  });
}

document.addEventListener('DOMContentLoaded', function() {
  var reportBtn = document.getElementById('reportBtn');
  if (reportBtn) {
    reportBtn.addEventListener('click', function(e) { openReportModal(e); });
  }
  document.getElementById('settingsModal').addEventListener('click', function(e) {
    if (e.target === this) closeSettings();
  });
});

// ── PIN helpers ──────────────────────────────────────────────────────────
function _clearBoxes(prefix) {
  for (let i = 0; i < 4; i++) { const el = document.getElementById(prefix+i); if(el) el.value=''; }
}
function _readBoxes(prefix) {
  return [0,1,2,3].map(i => { const el=document.getElementById(prefix+i); return el ? el.value : ''; }).join('');
}
function _pinNav(e, prefix, idx, onComplete) {
  if (e.key === 'Backspace') {
    e.preventDefault(); e.target.value = '';
    if (idx > 0) document.getElementById(prefix+(idx-1)).focus();
    return;
  }
  if (!/^[0-9]$/.test(e.key)) { e.preventDefault(); return; }
  e.preventDefault(); e.target.value = e.key;
  if (idx < 3) { document.getElementById(prefix+(idx+1)).focus(); }
  else { setTimeout(onComplete, 60); }
}

// ── PIN Entry modal (gate before settings opens) ──────────────────────────
function _showPinEntry() {
  const m = document.getElementById('pinEntryModal');
  document.body.appendChild(m);
  _clearBoxes('pe');
  document.getElementById('pinEntryError').textContent = '';
  m.classList.add('open');
  setTimeout(() => document.getElementById('pe0').focus(), 50);
}
function _closePinEntry() { document.getElementById('pinEntryModal').classList.remove('open'); }
function _submitPinEntry() {
  const v = _readBoxes('pe');
  if (v.length < 4) return;
  if (v === _getPin()) { _closePinEntry(); _loadSettingsData(); }
  else {
    const c = document.getElementById('pinEntryCard');
    c.classList.add('pin-shake'); setTimeout(() => c.classList.remove('pin-shake'), 450);
    document.getElementById('pinEntryError').textContent = 'Incorrect PIN. Try again.';
    _clearBoxes('pe'); setTimeout(() => document.getElementById('pe0').focus(), 50);
  }
}

// ── PIN Setup modal (set / change / remove) ───────────────────────────────
// _pinStep: 'verify' | 'new' | 'confirm'
let _pinStep = '', _pinFirst = '';
function openPinSetup() {
  const m = document.getElementById('pinSetupModal');
  document.body.appendChild(m);
  _pinFirst = '';
  const hasPin = !!_getPin();
  _pinStep = hasPin ? 'verify' : 'new';
  _clearBoxes('px');
  document.getElementById('pinSetupError').textContent = '';
  document.getElementById('pinRemoveBtn').style.display = 'none';
  if (!hasPin) {
    document.getElementById('pinSetupTitle').textContent = '🔒 Set PIN Lock';
    document.getElementById('pinSetupDesc').textContent  = 'Enter a 4-digit PIN';
  } else {
    document.getElementById('pinSetupTitle').textContent = '🔒 Change PIN';
    document.getElementById('pinSetupDesc').textContent  = 'Enter your current PIN';
  }
  m.classList.add('open');
  setTimeout(() => document.getElementById('px0').focus(), 50);
}
function closePinSetup() { document.getElementById('pinSetupModal').classList.remove('open'); }
function removePin() { _clearPin(); closePinSetup(); _updateLockBtn(); _showSettingsToast('PIN removed.'); }
function _pinSetupNext() {
  const v = _readBoxes('px');
  if (v.length < 4) return;
  const err = document.getElementById('pinSetupError');
  const card = document.getElementById('pinSetupCard');
  function shake() { card.classList.add('pin-shake'); setTimeout(()=>card.classList.remove('pin-shake'),450); }

  if (_pinStep === 'verify') {
    if (v !== _getPin()) {
      err.textContent = 'Incorrect PIN.'; shake();
      _clearBoxes('px'); setTimeout(() => document.getElementById('px0').focus(), 50);
      return;
    }
    _pinStep = 'new'; err.textContent = '';
    _clearBoxes('px');
    document.getElementById('pinSetupTitle').textContent = '🔒 Set New PIN';
    document.getElementById('pinSetupDesc').textContent  = 'Enter a new 4-digit PIN';
    document.getElementById('pinRemoveBtn').style.display = 'inline-block';
    setTimeout(() => document.getElementById('px0').focus(), 50);

  } else if (_pinStep === 'new') {
    _pinFirst = v; _pinStep = 'confirm'; err.textContent = '';
    _clearBoxes('px');
    document.getElementById('pinSetupTitle').textContent = '🔒 Confirm PIN';
    document.getElementById('pinSetupDesc').textContent  = 'Enter the PIN again to confirm';
    setTimeout(() => document.getElementById('px0').focus(), 50);

  } else if (_pinStep === 'confirm') {
    if (v !== _pinFirst) {
      err.textContent = 'PINs do not match — try again.'; shake();
      _pinStep = 'new'; _pinFirst = '';
      _clearBoxes('px');
      document.getElementById('pinSetupTitle').textContent = '🔒 Set PIN Lock';
      document.getElementById('pinSetupDesc').textContent  = 'Enter a 4-digit PIN';
      setTimeout(() => document.getElementById('px0').focus(), 50);
      return;
    }
    _savePin(v); closePinSetup(); _updateLockBtn(); _showSettingsToast('PIN lock set ✓');
  }
}
function _updateLockBtn() {
  const btn = document.getElementById('pinLockBtn');
  if (!btn) return;
  const has = !!_getPin();
  btn.textContent = has ? '🔒' : '🔓';
  btn.title = has ? 'PIN lock ON — click to change/remove' : 'No PIN — click to set lock';
}
function _showSettingsToast(msg) {
  const n = document.getElementById('saveNotice');
  if (!n) return;
  n.style.display = 'flex';
  const sp = n.querySelector('span');
  if (sp) sp.textContent = msg; else n.textContent = msg;
  setTimeout(() => n.style.display = 'none', 2500);
}
// Init lock icon on page load
window.addEventListener('DOMContentLoaded', _updateLockBtn);