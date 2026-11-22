import './style.css';

type Candidate = {
  record_id: string;
  score: string;
  tier: string;
  signals: Record<string, number>;
};
type ReviewItem = {
  document_id: string;
  filename: string;
  sender: string;
  reason: string;
  score: string | null;
  tier: string;
  candidates: Candidate[];
  rules_top_record_id: string | null;
  suggested_record_id: string | null;
};
type Trace = {
  filename: string;
  sender: string;
  status: string;
  reason: string;
  record_id: string | null;
  score: string | null;
  events: { step: string; detail: unknown }[];
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}
function block<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, ...children: (Node | string)[]) {
  const node = element(tag);
  node.className = className;
  node.append(...children);
  return node;
}
function unit(id: string) {
  return block('span', 'unit', id);
}
function button(text: string, action: () => void | Promise<void>, className = 'quiet') {
  const node = element('button', text);
  node.type = 'button';
  node.className = className;
  node.addEventListener('click', () => { void Promise.resolve().then(action).catch(showError); });
  return node;
}
// In the beta the page marks cookie access: the sandbox cookie goes with every request and no
// Authorization header is sent.
const cookieAccess = document.documentElement.dataset.access === 'cookie';
async function authorizedFetch(path: string, options?: RequestInit) {
  const headers = new Headers(options?.headers);
  if (!cookieAccess) {
    const token = sessionStorage.getItem('docket-review-token');
    if (!token) throw new Error('A reviewer token is required.');
    headers.set('Authorization', 'Bearer ' + token);
  }
  const response = await fetch(path, { ...options, headers, cache: 'no-store' });
  if (response.status === 401) {
    if (!cookieAccess) sessionStorage.removeItem('docket-review-token');
    window.location.reload();
    throw new Error('Reviewer access expired.');
  }
  return response;
}
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await authorizedFetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
const app = document.querySelector<HTMLDivElement>('#app')!;
const header = element('header');
header.className = 'top';
header.append(element('h1', 'Docket'), element('span', 'Document review'));
const message = element('p');
message.className = 'message';
message.setAttribute('role', 'status');
const layout = element('main');
layout.className = 'desk';
const queue = element('nav');
queue.setAttribute('aria-label', 'Review queue');
const detail = element('section');
detail.setAttribute('aria-label', 'Document');
const NO_COOKIES = 'Your sandbox needs a cookie to stay private to this browser, and this browser blocks cookies here. Allow cookies for this site and reload to send a sample.';
const DELETED = 'This sandbox is deleted.';

function cookiesWork() {
  if (!navigator.cookieEnabled) return false;
  const probe = 'docket_cookie_check=1';
  document.cookie = probe + '; Path=/; SameSite=Strict';
  const works = document.cookie.split('; ').includes(probe);
  document.cookie = 'docket_cookie_check=; Path=/; SameSite=Strict; Max-Age=0';
  return works;
}

// "Private to this browser · deleted in N minutes · Delete now". The server counts the
// seconds left; the page counts down from them between requests.
const sandboxLine = block('p', 'sample-note sandbox-line');
const deleteNow = button('Delete now', deleteSandbox, 'quiet small');
let sandboxEnds: number | undefined;
// Sending and Delete now never overlap, in this tab or another: a delete's answer spends the
// cookie, so a first send that finished during it would leave a sandbox this browser can no
// longer reach. Without the Web Locks API only this tab is covered.
let deleting = false;
let posting = false;
function oneAtATime<T>(work: () => Promise<T>): Promise<T> {
  return 'locks' in navigator ? navigator.locks.request('docket-sandbox', () => work()) : work();
}
let syncSample = () => {};
let recheckSamples = () => {};
function syncControls() {
  deleteNow.disabled = deleting || posting;
  syncSample();
}
// A sandbox that is gone leaves nothing on the page. Opening no document moves generation on,
// so a document still loading never draws over the empty pane.
function clearSandboxView() {
  items = [];
  selected = undefined;
  history.replaceState(null, '', window.location.pathname);
  void openDocument();
}
function showSandbox() {
  const minutes = sandboxEnds === undefined ? 0 : Math.ceil((sandboxEnds - Date.now()) / 60_000);
  if (sandboxEnds !== undefined && minutes <= 0) {
    // Expired: the sweep deletes it, so the page shows what is left, which is nothing.
    sandboxEnds = undefined;
    clearSandboxView();
    void refresh().catch(showError);
  }
  if (sandboxEnds === undefined) {
    sandboxLine.replaceChildren('Private to this browser · starts with your first sample');
    return;
  }
  sandboxLine.replaceChildren(
    'Private to this browser · ', `deleted in ${minutes} minute${minutes === 1 ? '' : 's'}`, ' · ', deleteNow);
}
async function loadSandbox() {
  const { expires_in } = await request<{ expires_in: number | null }>('/sandbox');
  sandboxEnds = expires_in === null ? undefined : Date.now() + expires_in * 1000;
  showSandbox();
}
// Deletes the browser's sandbox, whichever tab started it, empties the page and returns the
// answer to show.
async function deleteSandboxNow() {
  deleting = true;
  syncControls();
  let response: Response;
  try {
    response = await oneAtATime(() => authorizedFetch('/sandbox', { method: 'DELETE' }));
  } finally {
    deleting = false;
    syncControls();
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Delete now failed. Try again.');
  sandboxEnds = undefined;
  clearSandboxView();
  showSandbox();
  // The page is already empty, so a refresh that fails leaves the answer in place.
  void refresh().catch(() => {});
  return response.status === 202 && typeof body.detail === 'string' ? body.detail : DELETED;
}
async function deleteSandbox() {
  message.textContent = await deleteSandboxNow();
}
if (cookieAccess) window.setInterval(showSandbox, 15_000);

if (cookieAccess) {
  const side = block('div', 'side', samplePanel(), queue);
  layout.append(side, detail);
} else {
  layout.append(queue, detail);
}
app.append(header, message, layout);
let items: ReviewItem[] = [];
let selected: ReviewItem | undefined;
let generation = 0;
let busy = false;
let previewUrl: string | undefined;

function releasePreview() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = undefined;
}
window.addEventListener('pagehide', releasePreview);

function showError(error: unknown) {
  message.textContent = error instanceof Error ? error.message : 'Request failed';
}
// Plain-language versions of the rules' review reasons; the raw reason is shown beside them.
const HELD_BECAUSE: Record<string, string> = {
  conflicting_records: 'The clues name more than one unit outright, so a person picks.',
  insufficient_corroboration: 'The clues are not strong enough to file without a person.',
  no_candidate_chosen: 'No unit was found, so a person looks at it.',
  category_unreadable: 'The model could not label the document, so a person looks at it.',
  input_too_large: 'Part of the document is too long to match safely, so a person looks at it.',
};
const CLUE_NAMES: Record<string, string> = {
  explicit_id: 'Unit number',
  address: 'Address',
  tenant_name: 'Tenant',
  building: 'Building code',
  filename_id: 'Unit number in file name',
};
const TIER_WORDS: Record<string, string> = {
  strong: 'strong evidence',
  medium: 'some evidence',
  none: 'weak evidence, below the medium line',
};
// Plain-language versions of failure reasons a visitor can cause in the beta.
const FAILED_BECAUSE: Record<string, string> = {
  budget_exceeded: "Live answers stopped before this document finished. Today's budget ran out, or live answers were turned off.",
};
const short = (value: string | number) => Number(value).toFixed(3).replace(/0+$/, '').replace(/\.$/, '');

// The shortlist step records the conflict even when another hold reason wins (input too large,
// unreadable category). Older traces lack that step's flag, so the hold reason stands in.
function hasConflict(trace: Trace, heldReason: string) {
  const shortlist = trace.events.filter(event => event.step === 'shortlist').pop()?.detail;
  if (shortlist && typeof shortlist === 'object' && 'conflict' in shortlist) {
    return (shortlist as { conflict: unknown }).conflict === true;
  }
  return heldReason === 'conflicting_records';
}
function documentId() {
  const id = window.location.hash.slice(1);
  return /^[0-9a-f-]{36}$/i.test(id) ? id : undefined;
}
function renderQueue() {
  queue.replaceChildren(element('h2', 'Review queue'));
  const count = element('p', `${items.length} awaiting review · oldest first`);
  count.className = 'count';
  queue.append(count);
  queue.append(button('Refresh queue', () => { recheckSamples(); return refresh(); }, 'quiet small'));
  for (const item of items) {
    const link = element('a');
    link.href = `#${item.document_id}`;
    link.className = 'queue-item';
    if (item.document_id === documentId()) link.setAttribute('aria-current', 'page');
    link.append(element('strong', item.filename || 'Untitled document'), element('span', item.sender || 'Unknown sender'));
    queue.append(link);
  }
}
async function act(action: 'approve' | 'dismiss', value: string) {
  if (!selected || busy) return;
  const item = selected;
  busy = true;
  detail.querySelectorAll('button, select, textarea').forEach(node => (node as HTMLButtonElement).disabled = true);
  message.textContent = 'Saving decision…';
  try {
    await request(`/review/${item.document_id}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(action === 'approve' ? { record_id: value } : { reason: value }),
    });
    for (let attempt = 0; attempt < 120; attempt++) {
      const trace = await request<Trace>(`/documents/${item.document_id}`);
      if (!['received', 'processing', 'needs_review'].includes(trace.status)) {
        message.textContent = `Document ${trace.status}.`;
        await refresh();
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    message.textContent = 'Decision accepted; filing is still in progress. Refresh to check its status.';
  } finally {
    busy = false;
    await openDocument();
  }
}
function outcome(trace: Trace, conflict: boolean) {
  if (trace.status === 'needs_review') {
    const held = block('div', 'outcome held',
      element('strong', 'Waiting for a person'),
      element('p', HELD_BECAUSE[trace.reason] || 'Docket could not file it on its own, so a person decides.'));
    if (conflict && trace.reason !== 'conflicting_records') held.append(element('p', HELD_BECAUSE.conflicting_records));
    held.append(block('p', 'code', `Hold reason: ${trace.reason}`));
    return held;
  }
  if (trace.status === 'filed') {
    return block('div', 'outcome filed',
      element('strong', 'Filed'),
      element('p', trace.reason === 'review_approved' ? 'Approved by a reviewer.' : 'Filed by the rules: the clues agreed and the score reached the filing line.'));
  }
  if (trace.status === 'received' || trace.status === 'processing') {
    const reading = block('div', 'outcome', element('p', 'Docket is reading this document.'));
    if (trace.reason) reading.append(block('p', 'code', `Reason: ${trace.reason}`));
    return reading;
  }
  const plain = trace.status === 'failed' ? FAILED_BECAUSE[trace.reason] : undefined;
  if (plain) {
    return block('div', 'outcome',
      element('strong', 'Not finished'),
      element('p', plain),
      block('p', 'code', `Reason: ${trace.reason}`));
  }
  return block('div', 'outcome',
    element('strong', trace.status === 'dismissed' ? 'Dismissed' : 'Reason'),
    element('p', trace.reason || 'Processing'));
}
function pickCard(heading: string, note: string, id: string | null, empty: string, action: string) {
  const card = element('article');
  card.append(element('h3', heading), block('p', 'pick', id ? unit(id) : empty));
  if (id) card.append(block('p', 'note', note));
  const approve = button(action, () => act('approve', id!));
  approve.disabled = busy || !id;
  card.append(approve);
  return card;
}
function renderDocument(item: ReviewItem | undefined, trace: Trace, pdfUrl?: string) {
  detail.replaceChildren();
  const head = block('div', 'doc-head', element('h2', item?.filename || trace.filename || 'Document'));
  const state = element('p', `Status: ${trace.status}`);
  state.dataset.testid = 'document-status';
  state.className = 'status' + (trace.status === 'needs_review' ? ' held' : trace.status === 'filed' ? ' filed' : '');
  head.append(state);
  const facts = block('div', 'facts');
  facts.append(element('p', `Sender: ${item?.sender || trace.sender || 'Unknown sender'}`));
  if (trace.record_id) facts.append(block('p', '', 'Record: ', unit(trace.record_id)));
  head.append(facts);
  detail.append(head);

  const paper = block('div', 'paper');
  if (pdfUrl) {
    const preview = element('iframe');
    preview.title = 'PDF preview';
    preview.src = pdfUrl;
    paper.append(preview);
    const fileLink = element('a', 'Open PDF');
    fileLink.href = pdfUrl;
    fileLink.target = '_blank';
    fileLink.rel = 'noopener';
    paper.append(fileLink);
  } else {
    paper.append(block('p', 'no-preview', 'PDF preview unavailable.'));
  }
  const conflict = trace.status === 'needs_review' && hasConflict(trace, item?.reason ?? trace.reason);
  const evidence = block('div', 'evidence', outcome(trace, conflict));
  detail.append(block('div', 'sheet', paper, evidence));

  if (item && trace.status === 'needs_review') {
    evidence.append(element('h3', 'Candidates'));
    if (conflict && item.candidates.length) {
      evidence.append(block('p', 'list-note',
        'Every score here is set to 0 because the clues name more than one unit outright. The clues found are listed under each unit.'));
    }
    const candidates = element('ul');
    candidates.className = 'candidates';
    for (const candidate of item.candidates) {
      const row = element('li');
      const tags: string[] = [];
      if (candidate.record_id === item.rules_top_record_id) tags.push("Rules' top pick");
      if (candidate.record_id === item.suggested_record_id) tags.push('Model suggestion');
      row.append(
        unit(candidate.record_id),
        block('span', 'score', conflict
          ? 'Score 0, set by the conflict'
          : Number(candidate.score) === 0
            ? 'Score 0'
            : `Score ${short(candidate.score)} · ${TIER_WORDS[candidate.tier] || candidate.tier}`),
      );
      if (tags.length) row.append(block('span', 'tags', tags.join(' · ')));
      const found = Object.entries(candidate.signals).filter(([, value]) => value > 0);
      row.append(block('p', 'clues', found.length
        ? found.map(([signal, value]) => `${CLUE_NAMES[signal] || signal} ${value.toFixed(2)}`).join(' · ')
        : 'No clues'));
      candidates.append(row);
    }
    if (!item.candidates.length) candidates.append(block('li', 'none', 'No candidate units were found.'));
    evidence.append(candidates);

    const choices = element('div');
    choices.className = 'choices';
    choices.append(
      pickCard('Rules top candidate', conflict
        ? 'The rules list this unit first. Compare the candidates with the document.'
        : 'The rules list this unit first.',
        item.rules_top_record_id, 'No candidate', 'Approve rules top'),
      pickCard('Suggested', 'Suggested by the model. Nothing is filed unless you approve it.',
        item.suggested_record_id, 'No record suggested', 'Approve suggested'),
    );
    evidence.append(choices);

    const actions = block('div', 'actions');
    const pickLabel = element('label', 'Pick another candidate');
    const pick = element('select');
    pick.append(new Option('Choose a record', ''));
    item.candidates.forEach(candidate => pick.append(new Option(candidate.record_id, candidate.record_id)));
    pickLabel.append(pick);
    const approvePick = button('Approve selected', () => act('approve', pick.value));
    approvePick.disabled = true;
    pick.addEventListener('change', () => { approvePick.disabled = busy || !pick.value; });
    const dismissLabel = element('label', 'Dismiss reason');
    const reason = element('textarea');
    reason.rows = 2;
    dismissLabel.append(reason);
    const dismiss = button('Dismiss', () => act('dismiss', reason.value.trim()));
    dismiss.disabled = true;
    reason.addEventListener('input', () => { dismiss.disabled = busy || !reason.value.trim(); });
    actions.append(block('div', 'action', pickLabel, approvePick), block('div', 'action', dismissLabel, dismiss));
    evidence.append(actions);
  }
  const tracePanel = element('details');
  tracePanel.className = 'trace';
  tracePanel.append(element('summary', 'Document trace'), element('pre', JSON.stringify(trace, null, 2)));
  detail.append(tracePanel);
}
async function openDocument() {
  const token = ++generation;
  const id = documentId();
  renderQueue();
  if (!id) {
    releasePreview();
    detail.classList.toggle('flat', !items.length);
    detail.replaceChildren(block('div', 'pending',
      element('h2', items.length ? 'Select a document' : 'Nothing waiting'),
      element('p', items.length
        ? 'Open the oldest document in the queue, check the evidence, and choose where it belongs.'
        : 'Every held document has been settled. New ones appear in the queue; refresh to check.')));
    return;
  }
  const item = items.find(item => item.document_id === id) || (selected?.document_id === id ? selected : undefined);
  const trace = await request<Trace>(`/documents/${id}`);
  if (token !== generation) return;
  const response = await authorizedFetch(`/documents/${id}/file`);
  const blob = response.ok ? await response.blob() : undefined;
  if (token !== generation) return;
  releasePreview();
  if (blob) previewUrl = URL.createObjectURL(blob);
  selected = item;
  detail.classList.remove('flat');
  renderDocument(item, trace, previewUrl);
}
async function refresh() {
  items = (await request<{ items: ReviewItem[] }>('/review')).items;
  await openDocument();
}
type SampleOptions = { state: 'ready' | 'off' | 'paused' | 'catching_up'; case_types: { value: string; label: string }[] };
const SAMPLE_STATES: Record<string, string> = {
  off: 'Live answers are turned off right now.',
  paused: 'Live answers are paused for today. Try again tomorrow.',
  catching_up: 'Docket is catching up. Try again in a few minutes.',
};
const OUTCOME_WORDS: Record<string, string> = {
  filed: 'The sample was filed by the rules.',
  needs_review: 'The sample is waiting for review in the queue.',
  rejected: 'The sample was rejected.',
};


// The beta's only way in for a document: the server makes a synthetic PDF of the chosen case
// type and runs it with live model answers. The visitor sends nothing but the case type.
function samplePanel() {
  const panel = block('section', 'sample');
  panel.setAttribute('aria-labelledby', 'sample-title');
  const title = element('h2', 'Try a sample');
  title.id = 'sample-title';
  const note = block('p', 'sample-note',
    'Docket makes a synthetic PDF of the kind you pick and runs it with live model answers.');
  const label = element('label', 'Kind of document');
  const kinds = element('select');
  kinds.disabled = true;
  label.append(kinds);
  const send = element('button', 'Send a sample document');
  send.type = 'button';
  send.disabled = true;
  const state = block('p', 'sample-state');
  state.setAttribute('role', 'status');
  // Offered only when the sandbox is in its last minutes and refuses new samples.
  const fresh = element('button', 'Start fresh');
  fresh.type = 'button';
  fresh.className = 'quiet small';
  fresh.hidden = true;
  panel.append(title, note, label, send, state, fresh, sandboxLine);

  let ready = false;
  let inFlight = false;
  syncSample = () => {
    send.disabled = kinds.disabled = !ready || deleting || posting;
    fresh.disabled = inFlight || deleting || posting;
  };
  const settle = (canSend: boolean, text: string, offerFresh = false) => {
    ready = canSend;
    send.textContent = 'Send a sample document';
    state.textContent = text;
    fresh.hidden = !offerFresh;
    syncSample();
  };
  // Without the cookie, every sample would start a sandbox this browser could never open
  // again, so nothing is sent.
  if (!cookiesWork()) {
    label.hidden = send.hidden = sandboxLine.hidden = true;
    settle(false, NO_COOKIES);
    return panel;
  }
  void loadSandbox().catch(() => {});
  let listed = false;
  const loadOptions = () => request<SampleOptions>('/beta/samples').then(options => {
    if (!listed) kinds.replaceChildren(...options.case_types.map(item => new Option(item.label, item.value)));
    listed = true;
    settle(options.state === 'ready', SAMPLE_STATES[options.state] || '');
  });
  void loadOptions().catch(() => {
    // With no list there is nothing to pick or send, so only the status line remains.
    label.hidden = send.hidden = true;
    settle(false, 'Samples could not be loaded. Reload the page to try again.');
  });
  // A refusal says to try again in a few minutes, so the page asks again by itself, and on
  // Refresh queue. The server still refuses a send until it is ready.
  recheckSamples = () => {
    if (listed && !ready && !inFlight) void loadOptions().catch(() => {});
  };
  window.setInterval(recheckSamples, 30_000);

  const run = (work: () => Promise<SampleResult>) => {
    ready = false;
    inFlight = true;
    fresh.hidden = true;
    void work()
      .then(result => { inFlight = false; settle(result.canSend, result.text, result.startFresh); })
      .catch(() => { inFlight = false; settle(true, 'The sample could not be sent. Try again.'); });
  };
  send.addEventListener('click', () => {
    if (!ready || deleting) return;
    run(() => sendSample(kinds.value, send, kinds, state));
  });
  fresh.addEventListener('click', () => {
    if (inFlight || deleting || posting) return;
    const caseType = kinds.value;
    run(async () => {
      await deleteSandboxNow();
      return sendSample(caseType, send, kinds, state);
    });
  });
  return panel;
}
type SampleResult = { canSend: boolean; text: string; startFresh?: boolean };
async function sendSample(caseType: string, send: HTMLButtonElement, kinds: HTMLSelectElement, state: HTMLElement): Promise<SampleResult> {
  send.disabled = kinds.disabled = true;
  send.textContent = 'Sending…';
  state.textContent = '';
  posting = true;
  syncControls();
  let response: Response;
  try {
    response = await oneAtATime(() => authorizedFetch('/beta/samples', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_type: caseType }),
    }));
  } finally {
    posting = false;
    syncControls();
  }
  const body = await response.json().catch(() => ({}));
  const detail = typeof body.detail === 'string' ? body.detail : '';
  if (response.status === 503) return { canSend: false, text: detail || SAMPLE_STATES.paused };
  if (response.status === 409 && body.code === 'sandbox_ending') return { canSend: true, text: detail, startFresh: true };
  if (response.status === 409 && body.code === 'sandbox_ended') {
    // The answer spent the cookie, so nothing from that sandbox stays on the page.
    sandboxEnds = undefined;
    clearSandboxView();
    showSandbox();
    void refresh().catch(() => {});
    return { canSend: true, text: detail };
  }
  if (!response.ok) return { canSend: true, text: detail || 'The sample could not be sent. Try again.' };
  const id = String(body.document_id);
  state.textContent = 'Sample sent. Docket is reading it…';
  // The first sample starts the sandbox.
  void loadSandbox().catch(() => {});
  window.location.hash = id;
  // From here the sample exists, so no error below may suggest sending it again.
  try {
    for (let attempt = 0; attempt < 240; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const trace = await request<Trace>(`/documents/${id}`);
      if (['received', 'processing'].includes(trace.status)) continue;
      await refreshAfterSample(id);
      const failed = trace.status === 'failed' ? FAILED_BECAUSE[trace.reason] : undefined;
      return { canSend: true, text: OUTCOME_WORDS[trace.status] || failed || `The sample ended ${trace.status}.` };
    }
    return { canSend: true, text: 'The sample is still being read. Refresh the queue to check on it.' };
  } catch {
    return { canSend: true, text: 'The sample was sent. Refresh the queue to see it.' };
  }
}
// The visitor may have moved on to review another document while the sample was read, so only
// the queue is redrawn then, which keeps a half-written dismiss reason or a picked candidate.
// The pane is redrawn when it shows the sample or no document, since "Nothing waiting" may
// no longer be true.
async function refreshAfterSample(id: string) {
  items = (await request<{ items: ReviewItem[] }>('/review')).items;
  const open = documentId();
  if (open === id || open === undefined) await openDocument();
  else renderQueue();
}
window.addEventListener('hashchange', () => { void openDocument().catch(showError); });
void refresh().catch(showError);
