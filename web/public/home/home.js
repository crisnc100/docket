// Plays back recorded seed-11 results from demo.json. Nothing here scores a document:
// every figure comes from docket/front_demo.py, which tests/test_front_demo.py keeps in step
// with the committed report.

const CLUE_NAMES = {
  explicit_id: 'Unit number',
  address: 'Address',
  tenant_name: 'Tenant',
  building: 'Building code',
  filename_id: 'Unit number in file name',
};
const STEP_MS = 520;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    // The page's CSP blocks style attributes, so inline styles go through the CSSOM.
    else if (key === 'style') Object.assign(node.style, value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter(child => child !== null && child !== undefined));
  return node;
};
const p = (...children) => el('p', {}, ...children);
const unit = id => el('span', { class: 'num' }, id);
const short = value => Number(value).toFixed(3).replace(/0+$/, '').replace(/\.$/, '');

function readStep(doc) {
  if (!doc.text) {
    return [p('No text layer. This page is a picture of text, and Docket does not read images, so there is nothing to match.')];
  }
  const lines = doc.text.split('\n').length;
  return [p(`Read ${lines} lines of text from ${doc.pages} page${doc.pages === 1 ? '' : 's'}.`)];
}

function clueChips(candidate) {
  return Object.entries(candidate.signals).map(([name, strength]) =>
    strength < 1
      ? el('span', { class: 'clue part' }, `${CLUE_NAMES[name]}, close (${short(strength)})`)
      : el('span', { class: 'clue' }, CLUE_NAMES[name]),
  );
}

function findStep(doc) {
  if (doc.candidates.length === 0) {
    return [p('No unit matches anything in the text. With no candidate, the model is not asked either.')];
  }
  const strong = doc.candidates.filter(c => c.clue_count >= 2 || c.record_id === doc.rules.record_id);
  const rest = doc.candidates.filter(c => !strong.includes(c));
  const list = el('ul', { class: 'units' },
    ...strong.map(c => el('li', {},
      el('span', { class: 'id' }, c.record_id),
      el('span', { class: 'where' }, `${c.address} · ${c.tenant}`),
      el('span', { class: 'clues' }, ...clueChips(c)),
    )),
  );
  if (rest.length) {
    const shared = [...new Set(rest.flatMap(c => Object.keys(c.signals)))].map(name => CLUE_NAMES[name].toLowerCase());
    list.append(el('li', { class: 'weak' }, `${rest.length} more share only the ${shared.join(' and ')}.`));
  }
  const intro = doc.conflict
    ? p(`${doc.candidates.length} units found. The clues point at two different units.`)
    : p(`${doc.candidates.length} unit${doc.candidates.length === 1 ? '' : 's'} found.`);
  return [intro, list];
}

function meter(score, threshold) {
  const bar = el('div', { class: 'meter', role: 'img', 'aria-label': `Score ${short(score)} against a filing line of ${short(threshold)}` },
    el('span', { class: 'fill', style: { width: `${Math.min(score, 1) * 100}%` } }),
    el('span', { class: 'line', style: { left: `calc(${threshold * 100}% - 1px)` } }),
  );
  const labels = el('div', { class: 'meter-labels', 'aria-hidden': 'true' },
    el('span', {}, '0'),
    el('span', { class: 'at', style: { left: `${threshold * 100}%` } }, `files at ${short(threshold)}`),
    el('span', { class: 'end' }, '1'),
  );
  return [bar, labels];
}

function scoreStep(doc, policy) {
  const threshold = Number(policy.auto_file_threshold);
  if (doc.candidates.length === 0) return [p('Nothing to score.')];
  if (doc.conflict) {
    const best = [...doc.candidates].sort((a, b) => Number(b.evidence_score) - Number(a.evidence_score))[0];
    return [
      p('When clues point at two units, the score is set to 0, so the document cannot file on its own.'),
      p('Alone, ', unit(best.record_id), ` would have scored ${short(best.evidence_score)} from ${best.clue_count} clues.`),
    ];
  }
  const top = doc.candidates.find(c => c.record_id === doc.rules.record_id);
  const score = Number(doc.rules.score);
  const gap = threshold - score;
  const verdict = score >= threshold
    ? `${short(score)} from ${top.clue_count} clues. It needs ${policy.auto_file_threshold_short} and at least ${policy.clues_required} clues.`
    : `${short(score)} from ${top.clue_count} clues: ${gap.toFixed(4)} short of ${policy.auto_file_threshold_short}.`;
  return [...meter(score, threshold), p(verdict)];
}

const HELD_BECAUSE = {
  conflicting_records: 'Two units are named, so a person picks the right one.',
  no_candidate_chosen: 'No unit was found, so a person looks at it.',
  insufficient_corroboration: 'The clues are not strong enough to file without a person.',
  category_unreadable: 'The model could not label the document, so a person looks at it.',
};

function modelLine(doc) {
  const r = doc.recorded;
  if (!r.model_called) return 'The model was not asked: there was no candidate to suggest.';
  if (!r.model_suggestion) return 'The model suggested no unit.';
  return r.model_suggestion === doc.rules.record_id
    ? `The model also suggested ${r.model_suggestion}. A suggestion is shown to the reviewer, never used to file.`
    : `The model suggested ${r.model_suggestion}. A suggestion is shown to the reviewer, never used to file.`;
}

function labelLine(doc) {
  const expected = doc.expected.record_id;
  const comparison = doc.review_comparison;
  if (doc.recorded.status === 'would_file') return `The test label agrees: ${expected}.`;
  if (!expected) return 'The test label names no unit, so waiting is the right outcome.';
  if (comparison && !comparison.rules_correct && !comparison.suggestion_correct) {
    return `The test label says ${expected}. The rules' top pick and the model's suggestion were both wrong; holding the document kept it from being filed to the neighbour.`;
  }
  if (doc.expected.decision === 'filed') {
    return `The test label expected it to file to ${expected}. Both the rules and the model found it, so a reviewer only has to approve.`;
  }
  return `The test label says ${expected}.`;
}

function decideStep(doc) {
  const r = doc.recorded;
  const filed = r.status === 'would_file';
  const box = filed
    ? el('div', { class: 'outcome filed' }, el('strong', {}, `Filed to ${r.record_id}`), el('p', {}, `The rules decided: enough clues agree, and nothing points at another unit.`))
    : el('div', { class: 'outcome held' }, el('strong', {}, 'Waits for a person'), el('p', {}, HELD_BECAUSE[r.reason] ?? 'A person decides.'));
  return [box, el('p', { class: 'who-decided' }, modelLine(doc)), el('p', { class: 'who-decided' }, labelLine(doc))];
}

function markClues(pre, doc) {
  const phrases = new Set();
  for (const c of doc.candidates) {
    if (c.signals.explicit_id) phrases.add(c.record_id);
    if (c.signals.address) phrases.add(c.address);
    if (c.signals.tenant_name) phrases.add(c.tenant);
    if (c.signals.building) phrases.add(doc.text.match(/Building: (\S+)/)?.[1] ?? '');
  }
  const found = [...phrases].filter(phrase => phrase && doc.text.includes(phrase));
  pre.replaceChildren();
  if (!found.length) { pre.textContent = doc.text; return; }
  const pattern = new RegExp(found.map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g');
  let last = 0;
  for (const match of doc.text.matchAll(pattern)) {
    pre.append(doc.text.slice(last, match.index), el('mark', {}, match[0]));
    last = match.index + match[0].length;
  }
  pre.append(doc.text.slice(last));
}

function setup(data) {
  const docs = new Map(data.documents.map(doc => [doc.id, doc]));
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const panel = document.getElementById('run');
  const pre = panel.querySelector('[data-field="text"]');
  const filename = panel.querySelector('[data-field="filename"]');
  const steps = [...panel.querySelectorAll('[data-step]')];
  let timers = [];
  let current = 'doc-0001';

  function play(id, animate) {
    timers.forEach(clearTimeout);
    timers = [];
    current = id;
    const doc = docs.get(id);
    filename.textContent = doc.filename;
    pre.classList.toggle('empty', !doc.text);
    if (doc.text) markClues(pre, doc);
    else pre.textContent = 'No readable text in this PDF.';
    const content = {
      read: readStep(doc),
      find: findStep(doc),
      score: scoreStep(doc, data.policy),
      decide: decideStep(doc),
    };
    const instant = !animate || reducedMotion.matches;
    steps.forEach((step, index) => {
      step.querySelector('.detail').replaceChildren(...content[step.dataset.step]);
      step.classList.remove('done', 'reveal');
      step.classList.toggle('pending', !instant);
      if (instant) { step.classList.add('done'); return; }
      timers.push(setTimeout(() => {
        step.classList.remove('pending');
        step.classList.add('done', 'reveal');
      }, STEP_MS * (index + 1)));
    });
  }

  function select(tab, animate = true) {
    for (const other of tabs) {
      const on = other === tab;
      other.setAttribute('aria-selected', String(on));
      other.tabIndex = on ? 0 : -1;
    }
    panel.setAttribute('aria-labelledby', tab.id);
    play(tab.dataset.doc, animate);
  }

  for (const tab of tabs) {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      const visible = tabs.filter(t => !t.hidden);
      const index = visible.indexOf(tab);
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const next = visible[(index + step + visible.length) % visible.length];
      next.focus();
      select(next);
    });
  }

  const more = document.querySelector('[data-more]');
  more.addEventListener('click', () => {
    const hidden = tabs.find(tab => tab.hidden);
    hidden.hidden = false;
    more.remove();
    hidden.focus();
    select(hidden);
  });

  document.querySelector('[data-replay]').addEventListener('click', () => play(current, true));
  document.querySelector('[data-start-demo]').addEventListener('click', event => {
    event.preventDefault();
    const demo = document.getElementById('demo');
    demo.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
    tabs.find(tab => tab.getAttribute('aria-selected') === 'true').focus({ preventScroll: true });
    play(current, true);
  });

  play(current, false);
}

function fillFacts(data) {
  for (const node of document.querySelectorAll('[data-fact]')) {
    const value = node.dataset.fact.split('.').reduce((at, key) => at?.[key], data);
    if (value === undefined) continue;
    node.textContent = node.dataset.fact.startsWith('policy.weights.') ? Number(value).toFixed(2) : String(value);
  }
}

fetch('/home/demo.json', { cache: 'no-cache' })
  .then(response => {
    if (!response.ok) throw new Error(`demo.json: ${response.status}`);
    return response.json();
  })
  .then(data => {
    fillFacts(data);
    setup(data);
  })
  .catch(() => {
    const foot = document.querySelector('.demo-foot span');
    if (foot) foot.textContent = 'The recorded results did not load. Reload the page to try again.';
  });
