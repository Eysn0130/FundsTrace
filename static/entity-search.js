/* Case-bound providers and progressive enhancement for legacy entity inputs. */
(function (root) {
  'use strict';
  const normalize = value => String(value ?? '').normalize('NFKC').trim().toLocaleLowerCase();
  let revision = 0;
  const mounted = new Set();
  async function accounts({ caseId, source = 'flows', query, offset, limit, signal }) {
    const params = new URLSearchParams({ case_id: String(caseId), source, q: query, offset: String(offset), page_size: String(limit) });
    const response = await fetch('/api/account-suggestions?' + params, { signal });
    if (!response.ok) throw new Error('候选读取未完成');
    const data = await response.json();
    if (!data || typeof data !== 'object' || typeof data.case_id !== 'string' || data.case_id !== String(caseId) || data.source !== source || data.query !== query.normalize('NFKC').trim()
      || !Array.isArray(data.items) || data.items.length > limit || typeof data.has_more !== 'boolean'
      || (data.has_more ? data.items.length !== limit || data.next_offset !== offset + data.items.length : data.next_offset !== null)) throw new Error('候选上下文已失效');
    const seen = new Set();
    const items = data.items.map(item => {
      if (!item || typeof item !== 'object' || typeof item.account !== 'string' || !item.account || typeof item.name !== 'string'
        || (item.id_no != null && typeof item.id_no !== 'string') || typeof item.matched_text !== 'string'
        || !['account', 'id', 'name'].includes(item.matched_field) || seen.has(item.account)) throw new Error('候选格式不正确');
      seen.add(item.account);
      return { key: String(caseId) + ':' + item.account, label: item.name || item.account, value: item.account,
        account: item.account, idNo: item.id_no || undefined, matchedText: item.matched_text,
        matchedField: { account: 'account', id: 'idNo', name: 'label' }[item.matched_field],
        detail: item.matched_field === 'name' && item.matched_text !== item.name ? item.matched_text : undefined };
    });
    return { items, hasMore: data.has_more, nextOffset: data.next_offset ?? undefined };
  }
  function mount(input, options) {
    if (!input || !root.FundsEntityPicker) return null;
    const label = input.labels?.[0]?.textContent?.trim();
    if (!input.getAttribute('aria-label') && label) input.setAttribute('aria-label', label);
    const picker = root.FundsEntityPicker.create({ ...options, input, scope: () => {
      const scope = options.scope(); return scope == null ? null : JSON.stringify([scope, revision]);
    },
      search: options.search || (request => accounts({ ...request, caseId: options.caseId(), source: options.source || 'flows' })) });
    mounted.add(picker);
    const destroy = picker.destroy;
    picker.destroy = () => { mounted.delete(picker); destroy?.(); };
    if (options.action !== false && !input.closest('.find-search, .main-accounts-search')) {
      const doc = input.ownerDocument, wrap = doc.createElement('span'), button = doc.createElement('button');
      wrap.className = 'entity-search-field'; input.before(wrap); wrap.append(input, button);
      button.type = 'button'; button.className = 'entity-search-action'; button.setAttribute('aria-label', options.onSubmit ? '查询' : '选择账户');
      const icon = doc.createElement('img'); icon.src = '/static/ui-icons/search.svg'; icon.alt = ''; icon.className = 'ui-icon'; button.append(icon);
      button.addEventListener('click', event => { event.preventDefault(); if (input.disabled || input.readOnly) return;
        input.focus(); if (options.onSubmit) { picker.close(); options.onSubmit(input.value, picker.getSelected()); } else picker.open(); });
    }
    return picker;
  }
  function local(items, request) {
    const needle = normalize(request.query), matches = [];
    for (const item of items) {
      const fields = [['account', item.account], ['idNo', item.idNo], ['label', item.label]];
      const hit = fields.map(([field, text]) => ({ field, text: String(text || ''), normalized: normalize(text) }))
        .filter(value => value.normalized.includes(needle) && value.normalized)
        .map(value => ({ ...value, rank: value.normalized === needle ? 0 : value.normalized.startsWith(needle) ? 1 : 2 }))
        .sort((a, b) => a.rank - b.rank)[0];
      if (hit) matches.push({ ...item, matchedField: hit.field, matchedText: hit.text, rank: hit.rank });
    }
    matches.sort((a, b) => a.rank - b.rank || a.value.localeCompare(b.value));
    const end = request.offset + request.limit;
    return { items: matches.slice(request.offset, end), hasMore: end < matches.length, nextOffset: end };
  }
  root.FundsEntitySearch = { accounts, mount, local, get revision() { return revision; },
    invalidateAll() { revision++; for (const picker of mounted) picker.invalidate(); } };
})(typeof window !== 'undefined' ? window : globalThis);
