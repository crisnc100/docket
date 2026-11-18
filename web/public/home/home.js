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
    return [p('This PDF has no text layer, and Docket does not read page images, so there is nothing to match.')];
  }
  const lines = doc.text.split('\n').length;
  return [p(`Read ${lines} lines of text from ${doc.pages} page${doc.pages === 1 ? '' : 's'}.`)];
}

function clueChips(candidate) {
  const chips = Object.entries(candidate.signals).map(([name, strength]) =>
    strength < 1
      ? el('span', { class: 'clue part' }, `${CLUE_NAMES[name]}, close match (${short(strength)})`)
      : el('span', { class: 'clue' }, CLUE_NAMES[name]),
  );
  return chips.flatMap((chip, index) => (index ? [' · ', chip] : [chip]));
}

function weakNote(rest) {
  const shared = [...new Set(rest.flatMap(c => Object.keys(c.signals)))].map(name => CLUE_NAMES[name].toLowerCase());
  return `${rest.length} of them share only the ${shared.join(' and ')}.`;
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
  const found = `${doc.candidates.length} unit${doc.candidates.length === 1 ? '' : 's'} found`;
  const intro = doc.conflict
    ? p(`${found}. The clues point at two different units.`)
    : p(rest.length ? `${found}; ${weakNote(rest)}` : `${found}.`);
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
      p('Two units are named, so the score is set to 0. Alone, ', unit(best.record_id), ` would score ${short(best.evidence_score)} from ${best.clue_count} clues.`),
    ];
  }
  const top = doc.candidates.find(c => c.record_id === doc.rules.record_id);
  const score = Number(doc.rules.score);
  // A near miss shows score, line and gap at four places so the subtraction checks out.
  const shown = (score >= threshold || threshold - score >= 0.01) ? 3 : 4;
  const round = value => Number(value.toFixed(shown));
  const verdict = score >= threshold
    ? `${short(score)} from ${top.clue_count} clues. It needs ${policy.auto_file_threshold_short} and at least ${policy.clues_required} clues.`
    : `${round(score).toFixed(shown)} from ${top.clue_count} clues: ${(round(threshold) - round(score)).toFixed(shown)} short of ${round(threshold).toFixed(shown)}.`;
  return [...meter(score, threshold), p(verdict)];
}

const HELD_BECAUSE = {
  conflicting_records: 'Two units are named, so a person picks the right one.',
  no_candidate_chosen: 'No unit was found, so a person looks at it.',
  insufficient_corroboration: 'The clues are not strong enough to file without a person.',
  category_unreadable: 'The model could not label the document, so a person looks at it.',
};

function rulesValue(doc) {
  const r = doc.recorded;
  if (!doc.rules.record_id) return ['Held; no candidate unit'];
  return ['Held; top pick ', unit(doc.rules.record_id)];
}

function modelValue(doc) {
  const r = doc.recorded;
  if (!r.model_called) return ['Not asked: no candidate to suggest'];
  if (!r.model_suggestion) return ['None'];
  return [unit(r.model_suggestion)];
}

function labelValue(doc) {
  const expected = doc.expected.record_id;
  const comparison = doc.review_comparison;
  if (!expected) return ['No unit, so waiting is right'];
  if (doc.recorded.status === 'would_file') return [unit(expected), ', so the filing is right'];
  if (comparison && !comparison.rules_correct && !comparison.suggestion_correct) {
    return [unit(expected), '. Both picks were wrong; holding it prevented a wrong filing.'];
  }
  if (doc.expected.decision === 'filed') return [unit(expected), '. Both picks found it, so a reviewer only approves.'];
  return [unit(expected)];
}

function decideStep(doc) {
  const r = doc.recorded;
  const filed = r.status === 'would_file';
  const box = filed
    ? el('div', { class: 'outcome filed' }, el('strong', {}, `Filed to ${r.record_id}`), el('p', {}, 'Enough clues agree, and no other unit is named outright.'))
    : el('div', { class: 'outcome held' }, el('strong', {}, 'Waits for a person'), el('p', {}, HELD_BECAUSE[r.reason] ?? 'A person decides.'));
  // A filed document's rules row would repeat the green box, so only held ones show it.
  const ledger = el('dl', { class: 'ledger' },
    ...(filed ? [] : [el('dt', {}, 'Rules decided'), el('dd', {}, ...rulesValue(doc))]),
    el('dt', {}, 'Model suggested (not used to file)'), el('dd', {}, ...modelValue(doc)),
    el('dt', {}, 'Test label'), el('dd', {}, ...labelValue(doc)),
  );
  return [box, ledger];
}

function markClues(pre, doc) {
  // Spans come from the matcher re-run line by line in docket/front_demo.py, so a close
  // spelling is marked where it was actually matched.
  const found = [...new Set(doc.clue_spans.map(span => span.text))].filter(text => doc.text.includes(text));
  pre.replaceChildren();
  if (!found.length) { pre.textContent = doc.text; return; }
  found.sort((a, b) => b.length - a.length);
  const pattern = new RegExp(found.map(text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g');
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
  const track = panel.querySelector('[data-steps]');
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
    const outcome = doc.recorded.status === 'would_file' ? 'filed' : 'held';
    delete track.dataset.outcome;
    const settle = () => { track.dataset.outcome = outcome; };
    if (instant) settle();
    else timers.push(setTimeout(settle, STEP_MS * (steps.length + 1)));
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
      const index = tabs.indexOf(tab);
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const next = tabs[(index + step + tabs.length) % tabs.length];
      next.focus();
      select(next);
    });
  }

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
    document.querySelector('[data-demo-error]').hidden = false;
  });
