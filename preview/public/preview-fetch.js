/* Static, synthetic preview only. Production FastAPI and model calls are untouched. */
globalThis.__CQA_PREVIEW_BOOT__ = (async function installPreviewApi() {
  const nativeFetch = globalThis.fetch.bind(globalThis);
  const source = await nativeFetch('/preview-data.json');
  if (!source.ok) throw new Error('Не удалось загрузить синтетические данные предпросмотра.');
  const fixture = await source.json();
  const storageKey = 'cqa-native-static-preview-v3';
  const clone = value => structuredClone(value);
  const fresh = () => ({version: 3, cards: clone(fixture.cards), receipts: []});
  let state = fresh();

  const stable = value => Array.isArray(value) ? value.map(stable) :
    value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, stable(value[k])])) : value;
  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (saved?.version === 3 && Array.isArray(saved.cards) && Array.isArray(saved.receipts) &&
          saved.cards.length === fixture.cards.length && saved.cards.every((card, i) =>
            card?.evaluation?.evaluation_id === fixture.cards[i].evaluation.evaluation_id &&
            Number.isInteger(card.revision) && Array.isArray(card.appeals) && Array.isArray(card.final_answers))) state = saved;
    } catch { /* Preview still works in memory if browser storage is blocked. */ }
  }
  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* No server data is involved. */ }
  }
  restore();
  globalThis.__CQA_PREVIEW_RESET__ = () => { state = fresh(); persist(); };

  const reply = (status, value, headers = {}) => new Response(JSON.stringify(clone(value)), {
    status, headers: {'Content-Type': 'application/json; charset=utf-8', ...headers},
  });
  const error = (status, message) => reply(status, {detail: message});
  const findCard = id => state.cards.find(c => c.evaluation.evaluation_id === id);
  const allAppeals = () => state.cards.flatMap(c => c.appeals);
  const summary = c => ({evaluation_id: c.evaluation.evaluation_id,
    group_id: c.evaluation.group_id, conversation_id: c.evaluation.conversation_id,
    checklist_version_id: c.evaluation.checklist_version_id, status: c.evaluation.status,
    generation: c.evaluation.generation, revision: c.revision,
    original_total: c.evaluation.original_result?.total ?? null,
    final_total: c.evaluation.final_result?.total ?? null,
    job_id: c.runtime.job_id, created_at: c.runtime.started_at});
  const conversationSummary = ({transcript, audio_url, ...item}) => item;
  const only = (item, names) => item && typeof item === 'object' && !Array.isArray(item) &&
    Object.keys(item).every(key => names.includes(key));
  const requiredText = (text, max = 10000) => typeof text === 'string' && text.trim().length > 0 && text.length <= max;
  const sameValue = (a, b) => a === b && typeof a === typeof b;

  function precomputed(card, answers) {
    if (answers.some(a => a.decision_status !== 'evaluated')) return null;
    const ordered = card.checklist.criteria.map(rule => answers.find(a => a.criterion_id === rule.id)?.value);
    const id = fixture.calculations.by_version[card.checklist.checklist_version_id]?.[JSON.stringify(ordered)];
    if (id === undefined) throw new Error('Эта комбинация отсутствует в статическом предпросмотре. Используйте полный локальный сервер.');
    const [rows, sections, applicable_weight, earned_points, total, gross_error] = fixture.calculations.records[id];
    return clone({criteria: rows.map(i => fixture.calculations.rows[i]),
      sections: sections.map(i => fixture.calculations.sections[i]),
      applicable_weight, earned_points, total, gross_error});
  }

  function validateEvidence(evidence, conversation, required) {
    if (!Array.isArray(evidence) || (required && evidence.length === 0) || evidence.length > 200) return false;
    const positions = new Map(conversation.transcript.map((m, i) => [m.message_id, i]));
    return evidence.every(ref => {
      if (!only(ref, ['group_id', 'conversation_id', 'message_id', 'kind', 'span']) ||
          ref.group_id !== conversation.group_id || ref.conversation_id !== conversation.conversation_id ||
          !positions.has(ref.message_id) || !['support', 'absence', 'context'].includes(ref.kind)) return false;
      if (ref.span === null) return true;
      return only(ref.span, ['start_message_id', 'end_message_id']) &&
        positions.has(ref.span.start_message_id) && positions.has(ref.span.end_message_id) &&
        positions.get(ref.span.start_message_id) <= positions.get(ref.span.end_message_id);
    });
  }
  function flatEvidence(evidence, conversation) {
    const ids = new Set();
    for (const ref of evidence) {
      ids.add(ref.message_id);
      if (ref.span) {
        const start = conversation.transcript.findIndex(m => m.message_id === ref.span.start_message_id);
        const end = conversation.transcript.findIndex(m => m.message_id === ref.span.end_message_id);
        conversation.transcript.slice(start, end + 1).forEach(m => ids.add(m.message_id));
      }
    }
    return conversation.transcript.filter(m => ids.has(m.message_id)).map(m => ({
      group_id: conversation.group_id, conversation_id: conversation.conversation_id, message_id: m.message_id,
    }));
  }

  function jobs() {
    const ids = [...new Set(state.cards.map(c => c.runtime.job_id))];
    return ids.map(id => {
      const cards = state.cards.filter(c => c.runtime.job_id === id);
      const partial = cards.filter(c => c.evaluation.status === 'partial').length;
      return {job_id: id, parent_job_id: null, retry_job_id: null, group_id: 'SYNTHETIC',
        checklist_version_id: cards[0].evaluation.checklist_version_id,
        model_config: cards[0].runtime.model_config, created_at: cards[0].runtime.started_at,
        status: partial ? 'partial' : 'completed', total: cards.length,
        counts: {queued: 0, running: 0, completed: cards.length - partial, partial, failed: 0, interrupted: 0},
        items: cards.map(c => ({conversation_id: c.evaluation.conversation_id,
          status: c.evaluation.status, evaluation_id: c.evaluation.evaluation_id, error_code: null,
          started_at: c.runtime.started_at, finished_at: c.runtime.finished_at}))};
    });
  }

  globalThis.fetch = async function previewFetch(input, init = {}) {
    const req = typeof Request !== 'undefined' && input instanceof Request ? input : null;
    const url = new URL(req ? req.url : String(input), location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return nativeFetch(input, init);
    restore();
    const method = String(init.method || req?.method || 'GET').toUpperCase();
    const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent);
    const headers = new Headers(init.headers || req?.headers);
    let body = null;
    if (method !== 'GET' && method !== 'HEAD') {
      try { body = JSON.parse(init.body ?? (req ? await req.clone().text() : 'null')); }
      catch { return error(422, 'Неверный JSON.'); }
    }
    if (method === 'GET' && parts[1] === 'health') return reply(200, {status: 'ok', static_preview: true});
    if (method === 'GET' && parts[1] === 'capabilities') return reply(200, {
      model_available: false, audio_playback: false, acoustic_scoring: false, product_reference: false, static_preview: true,
    });
    if (method === 'GET' && parts[1] === 'model-status' && parts.length === 2) {
      return reply(200, fixture.model_status);
    }
    if (method === 'GET' && parts[1] === 'evaluation-runs') {
      if (parts.length === 2) {
        const group = url.searchParams.get('group_id');
        const conversation = url.searchParams.get('conversation_id');
        if (!group || !conversation || group.length > 200 || conversation.length > 200) {
          return error(422, 'Укажите группу и диалог для истории запусков.');
        }
        // Manually authored preview cards have no inference attempts or measured costs.
        return reply(200, []);
      }
      return error(404, 'Запуск модели не найден. Статический предпросмотр не вызывает AI.');
    }
    if (parts[1] === 'groups') {
      if (parts[2] !== 'SYNTHETIC') return error(404, 'В предпросмотре доступна только синтетическая группа SYNTHETIC.');
      if (method !== 'GET') return error(405, 'Импорт доступен в полной локальной сборке.');
      if (parts[3] !== 'conversations') return error(404, 'Маршрут не найден.');
      if (parts.length === 4) return reply(200, fixture.conversations.filter(c =>
        (!url.searchParams.get('language') || c.language === url.searchParams.get('language')) &&
        (!url.searchParams.get('channel') || c.channel === url.searchParams.get('channel'))).map(conversationSummary));
      const conversation = fixture.conversations.find(c => c.conversation_id === parts[4]);
      if (!conversation) return error(404, 'Диалог не найден.');
      if (parts.length === 5) return reply(200, conversation);
      if (parts[5] === 'messages' && parts.length === 7) {
        const message = conversation.transcript.find(m => m.message_id === parts[6]);
        return message ? reply(200, message) : error(404, 'Реплика не найдена.');
      }
      return error(404, 'В синтетическом предпросмотре нет записи звонка или подтверждённых аудиопозиций.');
    }
    if (method === 'GET' && parts[1] === 'evaluations') {
      if (parts.length === 2) return reply(200, state.cards.filter(c =>
        (!url.searchParams.get('group_id') || c.evaluation.group_id === url.searchParams.get('group_id')) &&
        (!url.searchParams.get('conversation_id') || c.evaluation.conversation_id === url.searchParams.get('conversation_id'))).map(summary));
      const card = findCard(parts[2]);
      return card ? reply(200, card.evaluation) : error(404, 'Оценка не найдена.');
    }
    if (method === 'GET' && parts[1] === 'evaluation-cards') {
      const card = findCard(parts[2]);
      if (!card) return error(404, 'Карточка не найдена.');
      if (parts.length === 3) return reply(200, card);
      if (parts[3] === 'audit') return reply(200, card,
        {'Content-Disposition': 'attachment; filename="cqa-preview-audit.json"'});
      return error(404, 'Маршрут не найден.');
    }
    if (method === 'GET' && parts[1] === 'appeals') return reply(200, allAppeals().filter(a =>
      !url.searchParams.get('status') || a.status === url.searchParams.get('status')));
    if (method === 'POST' && parts[1] === 'evaluations' && parts[3] === 'appeals') {
      const card = findCard(parts[2]);
      if (!card) return error(404, 'Оценка не найдена.');
      if (!only(body, ['criterion_id', 'comment']) || !requiredText(body.comment) ||
          !card.checklist.criteria.some(r => r.id === body.criterion_id)) return error(422, 'Укажите критерий и непустой комментарий.');
      const existing = card.appeals.find(a => a.criterion_id === body.criterion_id && a.status === 'open');
      if (existing) return reply(201, existing);
      const appeal = {appeal_id: 'preview-' + crypto.randomUUID(), evaluation_id: card.evaluation.evaluation_id,
        criterion_id: body.criterion_id, comment: body.comment.trim(), status: 'open',
        created_at: new Date().toISOString(), decision: null};
      card.appeals.push(appeal); persist(); return reply(201, appeal);
    }
    if (method === 'POST' && parts[1] === 'appeals' && parts[3] === 'decision') {
      const key = headers.get('Idempotency-Key');
      if (!key || key.length > 150) return error(422, 'Нужен Idempotency-Key.');
      const fingerprint = JSON.stringify(stable([url.pathname, body]));
      const receipt = state.receipts.find(r => r.key === key);
      if (receipt) return receipt.fingerprint === fingerprint ? reply(200, receipt.response) : error(409, 'Ключ уже использован с другим запросом.');
      const card = state.cards.find(c => c.appeals.some(a => a.appeal_id === parts[2]));
      const appeal = card?.appeals.find(a => a.appeal_id === parts[2]);
      if (!appeal) return error(404, 'Апелляция не найдена.');
      if (!only(body, ['action', 'value', 'applicability', 'reason', 'na_reason', 'evidence', 'expected_revision']) ||
          !['confirm', 'change'].includes(body.action) || !requiredText(body.reason) ||
          !Number.isInteger(body.expected_revision) || body.expected_revision < 0) return error(422, 'Проверьте поля решения.');
      if (body.expected_revision !== card.revision) return error(409, 'Карточка изменилась. Обновите результат.');
      if (appeal.status !== 'open') return error(409, 'Для этой апелляции уже сохранено решение.');
      const rule = card.checklist.criteria.find(r => r.id === appeal.criterion_id);
      const previous = card.final_answers.find(a => a.criterion_id === appeal.criterion_id);
      const conversation = fixture.conversations.find(c => c.conversation_id === card.evaluation.conversation_id);
      if (!['applicable', 'not_applicable'].includes(body.applicability)) return error(422, 'Укажите применимость критерия.');
      if (body.applicability === 'not_applicable') {
        if (!rule.allow_na || !rule.na_condition || body.value !== null || !requiredText(body.na_reason)) return error(422, 'N/A требует разрешения исходного правила и причины.');
      } else {
        if (body.na_reason !== null && body.na_reason !== undefined) return error(422, 'Причина N/A допустима только для N/A.');
        const valid = rule.answer_type === 'scale' ? typeof body.value === 'number' && Number.isFinite(body.value) &&
          rule.levels.some(level => level.value === body.value) : typeof body.value === 'boolean';
        if (!valid) return error(422, 'Значение отсутствует в исходной шкале критерия.');
      }
      if (!validateEvidence(body.evidence, conversation, rule.evidence_required)) return error(422, 'Проверьте доказательства: группа, диалог и диапазон должны совпадать.');
      const changed = previous.decision_status !== 'evaluated' || previous.applicability !== body.applicability || !sameValue(previous.value, body.value);
      if (body.action === 'confirm' && changed) return error(422, 'Подтверждение должно сохранять разрешённый исходный ответ.');
      if (body.action === 'change' && !changed) return error(422, 'Для изменения укажите другое значение или применимость.');
      const next = clone(card);
      const answer = next.final_answers.find(a => a.criterion_id === appeal.criterion_id);
      Object.assign(answer, {decision_status: 'evaluated', value: body.value, applicability: body.applicability,
        reason: body.reason.trim(), na_reason: body.na_reason?.trim() || null, evidence: clone(body.evidence)});
      let result;
      try { result = precomputed(next, next.final_answers); }
      catch (failure) { return error(422, failure.message); }
      const created_at = new Date().toISOString();
      const decision = {action: body.action, value: body.value, applicability: body.applicability,
        reason: body.reason.trim(), na_reason: body.na_reason?.trim() || null, evidence: clone(body.evidence),
        created_at, previous_value: previous.value, previous_applicability: previous.applicability,
        previous_decision_status: previous.decision_status};
      const nextAppeal = next.appeals.find(a => a.appeal_id === appeal.appeal_id);
      nextAppeal.status = 'resolved'; nextAppeal.decision = decision;
      next.revision += 1;
      next.final_status = result ? 'completed' : 'partial';
      next.evaluation.final_result = result;
      next.evaluation.review_history.push({appeal_id: appeal.appeal_id, criterion_id: appeal.criterion_id,
        checklist_version_id: next.evaluation.checklist_version_id, comment: appeal.comment,
        decision: body.action === 'confirm' ? 'confirmed' : 'changed', original_value: previous.value,
        new_value: body.value, reason: body.reason.trim(), revision: next.revision,
        evidence: flatEvidence(body.evidence, conversation)});
      state.cards[state.cards.indexOf(card)] = next;
      state.receipts.push({key, fingerprint, response: clone(next)}); persist();
      return reply(200, next);
    }
    if (method === 'GET' && parts[1] === 'evaluation-jobs') {
      const available = jobs().filter(j => !url.searchParams.get('group_id') || j.group_id === url.searchParams.get('group_id'));
      if (parts.length === 2) return reply(200, available);
      const job = available.find(j => j.job_id === parts[2]);
      return job ? reply(200, job) : error(404, 'Задание не найдено.');
    }
    if (method === 'POST' && parts[1] === 'submissions') return error(422,
      'Демонстрационные данные нельзя сдавать как results.json. Нужны настоящие запуски модели на полном наборе DEV или PROD в локальной сборке.');
    if (method === 'POST' && parts[1] === 'evaluation-jobs') return error(503,
      'Статический предпросмотр не запускает AI. Для оценки выбранной моделью запустите полную локальную сборку.');
    if (method === 'GET' && parts[1] === 'checklist-versions') {
      const version = fixture.checklists.find(v => v.checklist_version_id === parts[2]);
      return version ? reply(200, version) : error(404, 'Версия не найдена.');
    }
    if (method === 'GET' && parts[1] === 'checklists') {
      const versions = fixture.checklists.filter(v => v.checklist_id === parts[2]);
      if (parts.length === 2) return reply(200, fixture.checklist_summaries);
      if (parts[3] === 'versions') return reply(200, versions);
      if (parts[3] === 'draft' && versions[0]) {
        return reply(200, fixture.checklist_drafts[parts[2]]);
      }
    }
    return error(method === 'GET' ? 404 : 405,
      'Этот маршрут доступен в полной локальной сборке. Статический предпросмотр использует только синтетические данные.');
  };
})();
