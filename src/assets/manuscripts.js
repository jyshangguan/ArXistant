/* Manuscript workspace shares the paper reader, but owns its persisted conversations. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const parent = document.querySelector('.pane-right-inner');
  if (!parent) return;
  const style = document.createElement('style');
  style.textContent = `
    .mr-active > :not(.mr-workspace){display:none!important}
    .mr-workspace{display:none;height:100%;min-height:0;flex-direction:column;background:#fafcfb;font-size:13px}
    .mr-active > .mr-workspace{display:flex}
    .mr-top,.mr-compose{padding:10px;border-bottom:1px solid #dce5e0}
    .mr-top h2{font-size:16px;margin:0 0 6px}.mr-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:5px 0}
    .mr-workspace button,.mr-workspace select{font:inherit;padding:5px 8px;border:1px solid #c7d8d0;border-radius:5px;background:white;color:#234a3e;cursor:pointer}
    .mr-workspace button:disabled{opacity:.5;cursor:wait}.mr-workspace button.primary{background:#226e59;color:white}
    .mr-workspace input[type=text],.mr-workspace textarea{box-sizing:border-box;width:100%;font:inherit;padding:7px;border:1px solid #c7d8d0;border-radius:5px}
    .mr-workspace textarea{min-height:65px;resize:vertical}.mr-workspace summary{cursor:pointer}
    .mr-scroll{overflow:auto;flex:1;min-height:80px;padding:10px}.mr-card{border:1px solid #d5e1db;border-radius:7px;padding:9px;margin:8px 0;background:white}
    .mr-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5}.mr-muted{font-size:12px;color:#65766e}.mr-error{color:#a32b2b;white-space:pre-wrap}
    .mr-quote{border-left:3px solid #a6c8b5;padding-left:8px;color:#52685e;max-height:90px;overflow:auto}
    .mr-workspace ins{background:#dbf3df;text-decoration:none}.mr-workspace del{background:#ffe1de}.mr-selected{border-color:#267458}
    .mr-compose{border-top:1px solid #dce5e0;border-bottom:0}.mr-scope{max-height:65px;overflow:auto}.mr-workspace small{display:block}
    .mr-drop{margin:12px 0;padding:28px 14px;border:2px dashed #8cb7a5;border-radius:10px;text-align:center;background:#f3faf6;color:#315e4e}
    .mr-drop strong{display:block;font-size:15px;margin-bottom:6px}.mr-drop input{display:none}
    .mr-drop.mr-drag{border-color:#147354;background:#e2f5eb;box-shadow:0 0 0 3px #a8ddc4 inset}
  `;
  document.head.appendChild(style);
  const panel = document.createElement('section');
  panel.className = 'mr-workspace';
  panel.innerHTML = `
    <div class="mr-top"><div class="mr-row"><h2>Manuscript workspace</h2><button data-action="close" style="margin-left:auto">Back to chat</button></div>
      <div id="mrTitle" class="mr-muted"></div>
      <div class="mr-row"><button data-action="whole" class="primary">Review entire paper</button><button data-action="download">Download PDF</button></div>
      <details><summary>Review options & manuscript preferences</summary>
        <div class="mr-row"><label><input type="checkbox" id="mrLanguage" checked> Language</label><label><input type="checkbox" id="mrCitations" checked> Citations</label><label><input type="checkbox" id="mrFigures" checked> Figures/tables</label></div>
        <div class="mr-row"><label><input type="checkbox" id="mrLookup" checked> Look up cited sources</label><label><input type="checkbox" id="mrVision" checked> Inspect page images</label></div>
        <small class="mr-muted">Images require a vision-capable model. Lookups use ADS when configured, otherwise Semantic Scholar. Coverage gaps appear with each result.</small>
        <label>Journal, language, terminology and editing preferences<textarea id="mrPreferences" placeholder="For example: English comments; preserve technical terminology; concise edits."></textarea></label>
        <button data-action="preferences">Save preferences</button>
        <div class="mr-row"><select id="mrVersions" aria-label="Earlier manuscript version"></select><button data-action="compare">Check earlier comments</button></div>
      </details>
    </div>
    <div class="mr-scroll" id="mrScroll"><div id="mrError" class="mr-error" role="alert"></div><div id="mrSetup"></div><div id="mrResults"></div></div>
    <div class="mr-compose">
      <div id="mrScope" class="mr-scope mr-muted">Scope: selected pages, or entire paper</div>
      <div class="mr-row"><button data-action="selection">Use selected text</button><button data-action="clear">Clear target</button></div>
      <input id="mrPages" type="text" aria-label="Target pages" placeholder="Pages 2-4, section 3.1, or figure 2 (blank = entire paper)">
      <textarea id="mrMessage" aria-label="Manuscript revision request" placeholder="Discuss a comment, polish a passage, check a claim, or ask for alternatives…"></textarea>
      <div class="mr-row"><button data-action="send" class="primary">Send</button><button data-action="scope-review">Review this scope</button><span class="mr-muted">Ctrl/⌘ + Enter to send</span></div>
    </div>`;
  parent.appendChild(panel);
  const el = id => panel.querySelector('#' + id);
  const entry = document.createElement('button');
  entry.textContent = 'Manuscript'; entry.title = 'Review or revise an uploaded manuscript';
  entry.addEventListener('click', () => open());
  document.querySelector('.chat-header').appendChild(entry);
  const selectionButton = document.createElement('button');
  selectionButton.textContent = '✎ Revise';
  selectionButton.addEventListener('click', () => open(true));
  document.getElementById('selBubble')?.appendChild(selectionButton);
  let state = null, documentId = '', quote = '', findingId = '', visible = false, timer = null, requestBusy = false;
  let filter = 'open', categoryFilter = 'all', checked = new Set(), lastFingerprint = '';

  async function api(action, args = {}) {
    const response = await fetch('/api/manuscripts', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action,...args})});
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || 'Manuscript request failed');
    return result.data;
  }
  async function get(url) {
    const response = await fetch(url); const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || 'Could not load manuscript');
    return result.data;
  }
  function error(message) { el('mrError').textContent = message || ''; }
  function currentDocument() { return pinned && (pinned.local || pinned.source === 'local') ? (pinned.document_id || pinned.arxiv_id) : ''; }
  function uploadPrompt(message = '') {
    el('mrSetup').innerHTML = `<div class="mr-drop" id="mrDrop"><strong>${esc(message || 'Drop a manuscript PDF here')}</strong><span>or </span><button type="button" data-action="choose-file">Choose PDF</button><input id="mrFile" type="file" accept="application/pdf,.pdf"></div>`;
  }
  function droppedPdf(event) {
    return Array.from(event.dataTransfer?.files || []).find(file =>
      file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf'));
  }
  async function uploadManuscript(file) {
    if (!file) throw new Error('Choose a PDF file.');
    uploadPrompt('Importing “' + file.name + '”…');
    const imported = await uploadLocalPdf(file);
    if (!imported) {
      uploadPrompt('The PDF could not be imported. See the Conversation message for details.');
      return;
    }
    documentId = '';
    await open();
  }
  async function open(useSelection = false) {
    const id = currentDocument();
    parent.classList.add('mr-active'); visible = true;
    if (!id) {
      error('');
      uploadPrompt();
      schedule();
      return;
    }
    if (id !== documentId) {
      documentId = id; state = null; quote = ''; findingId = ''; checked.clear(); lastFingerprint = '';
      el('mrResults').replaceChildren(); el('mrPages').value = ''; el('mrMessage').value = '';
    }
    error(''); el('mrTitle').textContent = pinned.title || 'Manuscript';
    try {
      await refresh();
      if (!state.opened) {
        const workspaces = await get('/api/manuscripts');
        if (id !== documentId) return;
        el('mrSetup').innerHTML = `<div class="mr-card"><p>Start a workspace for this PDF, or link it as a new draft of an existing manuscript.</p><select id="mrLink" aria-label="Manuscript workspace"><option value="">New manuscript</option>${workspaces.map(w=>`<option value="${esc(w.id)}">${esc(w.title)}</option>`).join('')}</select><button data-action="initialize">Open workspace</button></div>`;
      } else {
        el('mrPreferences').value = state.workspace.preferences || '';
        const recent = [...state.jobs].reverse().find(j=>j.mode==='discuss');
        if (recent && !quote && !el('mrPages').value) { quote=recent.options.quote || ''; findingId=recent.options.finding_id || ''; el('mrPages').value=recent.options.pages || ''; }
      }
      if (useSelection) captureSelection();
      scopeLabel();
    } catch (e) { error(e.message); }
    schedule();
  }
  async function refresh() {
    if (!documentId) return;
    const id = documentId;
    const fresh = await get('/api/manuscripts/state?document=' + encodeURIComponent(id));
    if (id !== documentId) return;
    state = fresh;
    if (fresh.opened) {
      el('mrSetup').replaceChildren();
      const previous = el('mrVersions').value;
      el('mrVersions').innerHTML = fresh.versions.filter(v=>v.document!==id).map(v=>`<option value="${esc(v.document)}">${esc(v.title)} (${v.pages} pages)</option>`).join('');
      if (previous) el('mrVersions').value = previous;
      const fingerprint = JSON.stringify([fresh.jobs.map(j=>[j.id,j.updated]),fresh.findings.map(f=>[f.id,f.updated]),fresh.proposals.map(p=>[p.id,p.updated]),filter,categoryFilter]);
      if (fingerprint !== lastFingerprint && !panel.querySelector('[data-edit]')) { render(); lastFingerprint = fingerprint; }
      paint();
    }
  }
  function schedule() {
    clearTimeout(timer);
    if (!visible) return;
    timer = setTimeout(async () => {
      try {
        if (currentDocument() && currentDocument() !== documentId) await open();
        else await refresh();
      } catch (e) { error(e.message); }
      schedule();
    }, 1800);
  }
  function captureSelection() {
    let selected = activeSelection || pendingSelection || '';
    try {
      const selection = document.getElementById('textFrame').contentWindow.getSelection();
      if (selection?.toString().trim()) {
        selected = selection.toString();
        const node = selection.anchorNode;
        const page = (node?.nodeType === 1 ? node : node?.parentElement)?.closest('.pdf-page');
        if (page) el('mrPages').value = page.dataset.page;
      }
    } catch (_) {}
    if (!selected) throw new Error('Select text in the paper’s Text view, then click Use selected text.');
    quote = selected; findingId = ''; scopeLabel();
    hideSelectBubble();
  }
  function scopeLabel() {
    el('mrScope').textContent = findingId ? 'Discussing comment on page ' + el('mrPages').value : quote ? 'Selected passage: ' + quote : 'Scope: ' + (el('mrPages').value || 'entire paper');
  }
  function diff(original, replacement) {
    const a = original.split(/(\s+)/), b = replacement.split(/(\s+)/);
    let start=0, end=0;
    while (start<a.length && start<b.length && a[start]===b[start]) start++;
    while (end<a.length-start && end<b.length-start && a[a.length-1-end]===b[b.length-1-end]) end++;
    return esc(a.slice(0,start).join('')) + '<del>' + esc(a.slice(start,a.length-end).join('')) + '</del> <ins>' + esc(b.slice(start,b.length-end).join('')) + '</ins>' + esc(b.slice(b.length-end).join(''));
  }
  function render() {
    const scroll = el('mrScroll'), oldScroll = scroll.scrollTop;
    const jobs = state.jobs;
    let html = '';
    for (const job of jobs) {
      html += `<div class="mr-card"><strong>${esc(job.mode === 'review' ? 'Manuscript review' : job.mode === 'compare' ? 'Draft comparison' : 'Revision conversation')}</strong> · ${esc(job.status)} <span class="mr-muted">${job.cursor}/${job.total} batches</span>`;
      if (job.options.message) html += `<p class="mr-text">${esc(job.options.message)}</p>`;
      if (job.options.quote) html += `<div class="mr-quote">${esc(job.options.quote)}</div>`;
      if (job.error) html += `<p class="mr-error">${esc(job.error)}</p>`;
      if (job.status==='failed' && job.cursor<job.total) html += `<p class="mr-error"><b>The review is incomplete.</b> Pages after the last completed batch have not been checked yet.</p>`;
      if (['running','queued'].includes(job.status)) html += `<button data-action="cancel" data-id="${job.id}">Cancel</button>`;
      if (['failed','interrupted','cancelled'].includes(job.status)) html += `<button data-action="resume" data-id="${job.id}">Resume</button>`;
      if (job.warnings.length) html += `<details><summary>Coverage & limitations (${job.warnings.length})</summary><ul>${job.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></details>`;
      if (job.summary) html += `<p class="mr-text"><b>Review summary</b><br>${esc(job.summary)}</p>`;
      if (job.mode==='discuss') {
        for (const answer of job.answers) html += `<div class="mr-text">${esc(answer.text)}</div>`;
      } else if (job.answers.length) html += `<details><summary>Review notes by chunk</summary>${job.answers.map(a=>`<p><b>${a.pass==='visual'?'Visual check · ':a.pass==='citation'?'Source check · ':''}${a.pages?.length>1?'Pages '+a.pages[0]+'–'+a.pages[a.pages.length-1]:'Page '+a.page}</b></p><div class="mr-text">${esc(a.text)}</div>`).join('')}</details>`;
      if (job.comparisons.length) html += `<details open><summary>Earlier comments — proposed assessments</summary>${job.comparisons.map(c=>`<p><b>${esc(c.assessment)}</b> (checked on p. ${c.page})<br><i>${esc(c.original_comment)}</i><br>${esc(c.reason)}${c.location?`<br><button data-action="carry" data-id="${esc(c.finding_id)}">Carry comment to page ${c.location.page}</button>`:''}</p>`).join('')}<small>Assessments do not automatically resolve or move earlier comments.</small></details>`;
      for (const proposal of state.proposals.filter(p=>p.job===job.id)) {
        html += `<div class="mr-card"><b>Proposal ${state.proposals.filter(p=>p.job===job.id).indexOf(proposal)+1}</b> · ${esc(proposal.status)} · p. ${proposal.page}<div class="mr-text">${diff(proposal.original,proposal.replacement)}</div><p>${esc(proposal.reason)}</p><div class="mr-row"><button data-action="copy" data-id="${proposal.id}">Copy wording</button><button data-action="choose" data-id="${proposal.id}">Use this proposal</button><button data-action="annotate" data-id="${proposal.id}">Add to paper comments</button></div><small class="mr-muted">Working text; apply it in your manuscript editor.</small></div>`;
      }
      html += '</div>';
    }
    html += `<h3>Paper comments (${state.findings.length})</h3><div class="mr-row"><select id="mrFilter" aria-label="Comment status">${['open','all','resolved','dismissed'].map(v=>`<option${v===filter?' selected':''}>${v}</option>`).join('')}</select><select id="mrCategory" aria-label="Comment category">${['all','language','citation','figure','structure','existing'].map(v=>`<option${v===categoryFilter?' selected':''}>${v}</option>`).join('')}</select><button data-action="download-selected">Export selected</button></div>`;
    const findings = state.findings.filter(f=>(filter==='all'||f.status===filter)&&(categoryFilter==='all'||f.category===categoryFilter)).sort((a,b)=>a.page-b.page);
    for (const f of findings) {
      html += `<article class="mr-card${findingId===f.id?' mr-selected':''}" id="mr-f-${f.id}"><div class="mr-row"><input type="checkbox" data-check="${f.id}" aria-label="Select comment for export" ${checked.has(f.id)?'checked':''}><button data-action="locate" data-id="${f.id}">Page ${f.page}</button><b>${esc(f.category)}</b> · ${esc(f.severity)} · ${esc(f.status)}</div><div class="mr-quote">${esc(f.quote || 'Page / figure note')}</div><p class="mr-text">${esc(f.comment)}</p>${f.replacement?`<div class="mr-text"><ins>${esc(f.replacement)}</ins></div>`:''}<small class="mr-muted">${esc(f.placement)}</small>${f.evidence.length?`<details><summary>Source evidence</summary><pre class="mr-text">${esc(JSON.stringify(f.evidence,null,2))}</pre></details>`:''}<div class="mr-row"><button data-action="discuss" data-id="${f.id}">Discuss</button><button data-action="edit" data-id="${f.id}">Edit</button><button data-action="resolve" data-id="${f.id}">${f.status==='resolved'?'Reopen':'Resolve'}</button><button data-action="dismiss" data-id="${f.id}">Dismiss</button></div></article>`;
    }
    if (!jobs.length) html = '<p>Select a passage to revise, choose pages to discuss, or review the entire paper.</p>' + html;
    el('mrResults').innerHTML = html;
    scroll.scrollTop = oldScroll;
  }
  async function start(mode, whole=false) {
    if (!state?.opened) throw new Error('Open a workspace first.');
    const message = el('mrMessage').value.trim();
    if (mode==='discuss' && /^(?:\/review-manuscript|review (?:the )?(?:entire|whole) (?:paper|manuscript))/i.test(message)) { mode='review';whole=true; }
    if (mode==='discuss' && !message) throw new Error('Enter a question or revision request.');
    let categories = [['mrLanguage','language'],['mrCitations','citation'],['mrFigures','figure']].filter(([id])=>el(id).checked).map(([,v])=>v);
    if (mode==='discuss') {
      const requested = ['language','structure'];
      const active = state.findings.find(f=>f.id===findingId);
      if (/citat|reference|source|support.*claim|claim.*support/i.test(message) || active?.category==='citation') requested.push('citation');
      if (/figure|\bfig\.|table|caption|legend|plot/i.test(message) || active?.category==='figure') requested.push('figure');
      categories = requested;
    }
    const options = {mode,message,categories, whole, pages:whole?'':el('mrPages').value, quote:whole?'':quote, finding_id:whole?'':findingId,
      lookup:el('mrLookup').checked,vision:el('mrVision').checked};
    if (mode==='compare') { options.previous_document=el('mrVersions').value; options.pages=''; options.quote=''; }
    const job = await api('start',{document:documentId,options});
    if (mode==='discuss' && job.options.pages) { el('mrPages').value=job.options.pages;scopeLabel(); }
    el('mrMessage').value=''; await refresh();
  }
  async function locateFinding(f) {
    if (currentDocument() !== documentId) throw new Error('Reopen this PDF in the paper reader first.');
    switchView('text');
    for (let n=0;n<50;n++) {
      const doc = document.getElementById('textFrame').contentDocument;
      const page = doc?.querySelector(`.pdf-page[data-page="${f.page}"]`);
      if (page) { paint(); page.scrollIntoView({behavior:'smooth',block:'start'}); return; }
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw new Error('The paper reader is still loading. Try the page button again.');
  }
  function paint() {
    if (!state?.opened || currentDocument() !== documentId) return;
    const doc = document.getElementById('textFrame')?.contentDocument;
    if (!doc?.body?.dataset.pdfReady) return;
    const signature = state.findings.map(f=>f.id+':'+f.revision).join('|');
    if (doc.body.dataset.reviewSignature === signature) return;
    doc.body.dataset.reviewSignature = signature;
    doc.querySelectorAll('.mr-mark').forEach(n=>n.remove());
    const counts = {};
    for (const f of state.findings.filter(f=>f.status==='open')) {
      const page = doc.querySelector(`.pdf-page[data-page="${f.page}"]`);
      if (!page) continue;
      for (const rect of f.origin === 'existing' ? [] : f.view_rects || []) {
        const mark = doc.createElement('span'); mark.className='mr-mark';
        Object.assign(mark.style,{position:'absolute',zIndex:3,pointerEvents:'none',background:'#ffd45066',left:rect[0]*100+'%',top:rect[1]*100+'%',width:(rect[2]-rect[0])*100+'%',height:(rect[3]-rect[1])*100+'%'});
        page.appendChild(mark);
      }
      const index = counts[f.page] || 0; counts[f.page]=index+1;
      const badge = doc.createElement('button'); badge.className='mr-mark'; badge.textContent='✎'+(index+1); badge.title=f.comment;
      const rect = f.view_rects?.[0];
      Object.assign(badge.style,{position:'absolute',zIndex:5,right:'2px',top:rect?rect[1]*100+'%':(24+index*24)+'px',fontSize:'10px',background:'#fff3b5',border:'1px solid #c99e40',cursor:'pointer'});
      badge.onclick=()=>{ filter='all'; categoryFilter='all'; render(); panel.querySelector('#mr-f-'+f.id)?.scrollIntoView({block:'nearest'}); };
      page.appendChild(badge);
    }
  }
  async function download(selected) {
    if (!state?.opened) throw new Error('Open a workspace first.');
    if (selected && !checked.size) throw new Error('Select the comments to export.');
    const response = await fetch('/api/manuscripts/export?document='+encodeURIComponent(documentId)+(selected?'&ids='+encodeURIComponent([...checked].join(',')):''));
    if (!response.ok) { const value=await response.json(); throw new Error(value.error); }
    const url=URL.createObjectURL(await response.blob()), link=document.createElement('a');
    link.href=url; link.download=(state.workspace.title || 'manuscript').replace(/[^\w\s.-]/g,'_')+'_reviewed.pdf'; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  panel.addEventListener('change', event=>{
    if (event.target.id==='mrFile') {
      uploadManuscript(event.target.files?.[0]).catch(e=>error(e.message));
      return;
    }
    if (event.target.dataset.check) { if(event.target.checked) checked.add(event.target.dataset.check); else checked.delete(event.target.dataset.check); }
    if (event.target.id==='mrFilter') { filter=event.target.value; render(); }
    if (event.target.id==='mrCategory') { categoryFilter=event.target.value; render(); }
    if (event.target.id==='mrPages') { quote='';findingId='';scopeLabel(); }
  });
  panel.addEventListener('click', async event=>{
    const button=event.target.closest('button[data-action]'); if(!button || requestBusy) return;
    const action=button.dataset.action,id=button.dataset.id;
    requestBusy=true; button.disabled=true; error('');
    try {
      const f=state?.findings?.find(f=>f.id===id);
      if(action==='close') { parent.classList.remove('mr-active');visible=false;clearTimeout(timer); }
      else if(action==='choose-file') { el('mrFile')?.click(); }
      else if(action==='initialize') { await api('open',{document:documentId,workspace:el('mrLink').value || undefined}); await refresh(); el('mrPreferences').value=state.workspace.preferences || ''; }
      else if(action==='selection') captureSelection();
      else if(action==='clear') { quote='';findingId='';el('mrPages').value='';scopeLabel(); }
      else if(action==='whole') await start('review',true);
      else if(action==='scope-review') await start('review');
      else if(action==='send') await start('discuss');
      else if(action==='compare') await start('compare');
      else if(action==='download') await download(false);
      else if(action==='download-selected') await download(true);
      else if(action==='preferences') { await api('preferences',{id:state.workspace.id,preferences:el('mrPreferences').value}); }
      else if(action==='discuss') { quote='';findingId=f.id;el('mrPages').value=String(f.page);scopeLabel();el('mrMessage').focus(); }
      else if(action==='locate') await locateFinding(f);
      else if(action==='carry') { await api('carry',{id,document:documentId}); await refresh(); }
      else if(action==='copy') await navigator.clipboard.writeText(state.proposals.find(p=>p.id===id).replacement);
      else if(action==='edit') {
        const card=button.closest('article');
        const editor=document.createElement('div');
        editor.innerHTML=`<label>Comment<textarea data-edit="comment">${esc(f.comment)}</textarea></label><label>Suggested wording<textarea data-edit="replacement">${esc(f.replacement)}</textarea></label><button data-action="save-edit" data-id="${id}">Save comment</button>`;
        card.appendChild(editor);button.remove();
      } else if(action==='save-edit') {
        const card=button.closest('article');
        await api('finding',{id,revision:f.revision,comment:card.querySelector('[data-edit=comment]').value,replacement:card.querySelector('[data-edit=replacement]').value}); card.querySelector('[data-edit]').parentElement.parentElement.remove(); await refresh();
      } else if(action==='resolve'||action==='dismiss') { await api('finding',{id,revision:f.revision,status:action==='dismiss'?'dismissed':f.status==='resolved'?'open':'resolved'}); await refresh(); }
      else { await api(action,{id}); await refresh(); }
    } catch(e) { error(e.message); }
    finally { requestBusy=false;button.disabled=false; }
  });
  panel.addEventListener('dragenter', event=>{
    if (!Array.from(event.dataTransfer?.items || []).some(item=>item.kind==='file')) return;
    event.preventDefault();
    panel.querySelector('#mrDrop')?.classList.add('mr-drag');
  });
  panel.addEventListener('dragover', event=>{
    if (!Array.from(event.dataTransfer?.items || []).some(item=>item.kind==='file')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect='copy';
    panel.querySelector('#mrDrop')?.classList.add('mr-drag');
  });
  panel.addEventListener('dragleave', event=>{
    if (!panel.contains(event.relatedTarget)) panel.querySelector('#mrDrop')?.classList.remove('mr-drag');
  });
  panel.addEventListener('drop', event=>{
    event.preventDefault();
    panel.querySelector('#mrDrop')?.classList.remove('mr-drag');
    uploadManuscript(droppedPdf(event)).catch(e=>error(e.message));
  });
  el('mrMessage').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();panel.querySelector('[data-action=send]').click();}});
})();
