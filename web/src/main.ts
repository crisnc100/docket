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
function button(text: string, action: () => void | Promise<void>) {
  const node = element('button', text);
  node.type = 'button';
  node.addEventListener('click', () => { void Promise.resolve().then(action).catch(showError); });
  return node;
}
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
const app = document.querySelector<HTMLDivElement>('#app')!;
const header = element('header');
header.append(element('h1', 'Docket'), element('span', 'Document review'));
const message = element('p');
message.className = 'message';
message.setAttribute('role', 'status');
const layout = element('main');
const queue = element('nav');
queue.setAttribute('aria-label', 'Review queue');
const detail = element('section');
detail.setAttribute('aria-label', 'Document');
layout.append(queue, detail);
app.append(header, message, layout);
let items: ReviewItem[] = [];
let selected: ReviewItem | undefined;
let generation = 0;
let busy = false;

function showError(error: unknown) {
  message.textContent = error instanceof Error ? error.message : 'Request failed';
}
function documentId() {
  const id = window.location.hash.slice(1);
  return /^[0-9a-f-]{36}$/i.test(id) ? id : undefined;
}
function renderQueue() {
  queue.replaceChildren(element('h2', 'Review queue'));
  queue.append(element('p', `${items.length} awaiting review · oldest first`));
  queue.append(button('Refresh queue', refresh));
  if (!items.length) queue.append(element('p', 'No documents awaiting review.'));
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
function renderDocument(item: ReviewItem | undefined, trace: Trace, id: string) {
  detail.replaceChildren(element('h2', item?.filename || 'Document'));
  const state = element('p', `Status: ${trace.status}`);
  state.dataset.testid = 'document-status';
  detail.append(state, element('p', `Reason: ${trace.reason || 'Processing'}`));
  if (item) detail.append(element('p', `Sender: ${item.sender || 'Unknown sender'}`));
  if (trace.record_id) detail.append(element('p', `Record: ${trace.record_id}`));
  const preview = element('iframe');
  preview.title = 'PDF preview';
  preview.src = `/documents/${id}/file`;
  detail.append(preview);
  const fileLink = element('a', 'Open PDF');
  fileLink.href = `/documents/${id}/file`;
  fileLink.target = '_blank';
  fileLink.rel = 'noopener';
  detail.append(fileLink);
  if (item && trace.status === 'needs_review') {
    const choices = element('div');
    choices.className = 'choices';
    const suggested = element('article');
    suggested.append(element('h3', 'Suggested'), element('p', item.suggested_record_id || 'No record suggested'));
    const approveSuggestion = button('Approve suggested', () => act('approve', item.suggested_record_id!));
    approveSuggestion.disabled = busy || !item.suggested_record_id;
    suggested.append(approveSuggestion);
    const rules = element('article');
    rules.append(element('h3', 'Rules top candidate'), element('p', item.rules_top_record_id || 'No candidate'));
    const approveRules = button('Approve rules top', () => act('approve', item.rules_top_record_id!));
    approveRules.disabled = busy || !item.rules_top_record_id;
    rules.append(approveRules);
    choices.append(suggested, rules);
    detail.append(choices, element('h3', 'Candidates'));
    const candidates = element('ul');
    for (const candidate of item.candidates) {
      const row = element('li');
      row.append(element('strong', candidate.record_id), element('span', ` · Score ${candidate.score} · ${candidate.tier}`));
      row.append(element('p', Object.entries(candidate.signals).map(([signal, value]) => `${signal}: ${value}`).join(' · ')));
      candidates.append(row);
    }
    detail.append(candidates);
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
    detail.append(pickLabel, approvePick, dismissLabel, dismiss);
  }
  const tracePanel = element('details');
  tracePanel.append(element('summary', 'Document trace'), element('pre', JSON.stringify(trace, null, 2)));
  detail.append(tracePanel);
}
async function openDocument() {
  const token = ++generation;
  const id = documentId();
  renderQueue();
  if (!id) {
    detail.replaceChildren(element('h2', 'Select a document'), element('p', 'Review the evidence and choose where it belongs.'));
    return;
  }
  const item = items.find(item => item.document_id === id) || (selected?.document_id === id ? selected : undefined);
  const trace = await request<Trace>(`/documents/${id}`);
  if (token !== generation) return;
  selected = item;
  renderDocument(item, trace, id);
}
async function refresh() {
  items = (await request<{ items: ReviewItem[] }>('/review')).items;
  await openDocument();
}
window.addEventListener('hashchange', () => { void openDocument().catch(showError); });
void refresh().catch(showError);
