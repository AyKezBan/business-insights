'use strict';
const $ = id => document.getElementById(id);
const state = {data: null, ctx: null, epoch: 0, pageController: null, questionController: null, card: null, questionSeq: 0, profile: null, preferences: {}};
const node = (tag, text = '', className = '') => {
  const n = document.createElement(tag); n.textContent = text; n.className = className; return n;
};
const status = text => { $('status').textContent = text; };
const labels = {fact: 'Факт события', open: 'Аналитика по открытым источникам', bank: 'Аналитика от Альфа-банка', ai: 'Вывод для компании'};
const certaintyLabels = {fact: 'Факт', inference: 'Вывод модели по источникам', hypothesis: 'Гипотеза · требует проверки', assessment: 'Оценка в аналитическом материале', recommendation: 'Рекомендация · требует проверки данных', unavailable: 'Нет данных'};
let api = {mode: 'demo'};
const pendingOperations = new Map();
const PROFILE_KEY = 'd09-d10-profile-v1';
const PREFERENCES_KEY = 'd09-d10-preferences-v1';
function readStored(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; } }
function saveStored(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
function normalizePreferences(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const validFeedback = new Set(['useful', 'not_useful']);
  const validReasons = new Set(['company', 'basis', 'error', 'decision', 'other']);
  return Object.fromEntries(Object.entries(value).flatMap(([key, record]) => {
    if (!record || typeof record !== 'object' || Array.isArray(record)) return [];
    const clean = {};
    if (typeof record.hidden === 'boolean') clean.hidden = record.hidden;
    if (validFeedback.has(record.feedback)) {
      clean.feedback = record.feedback;
      if (record.reason === null || validReasons.has(record.reason)) clean.reason = record.reason;
      if (record.previousNegativeReason === null || validReasons.has(record.previousNegativeReason)) clean.previousNegativeReason = record.previousNegativeReason;
    }
    return Object.keys(clean).length ? [[key, clean]] : [];
  }));
}
function preferenceKey(eventId) { return state.profile.company_id + ':' + state.profile.role + ':' + eventId; }
function feedbackKey(card) { return preferenceKey(card.context.event_id) + ':' + card.insight_id; }
function setPreference(eventId, value) {
  const key = preferenceKey(eventId);
  const updated = {...state.preferences, [key]: {...state.preferences[key], ...value}};
  if (!saveStored(PREFERENCES_KEY, updated)) { showError('Не удалось сохранить изменение в этом браузере. Проверьте разрешение локального хранилища.'); return false; }
  state.preferences = updated; return true;
}


function routeURL(page, extra = {}, ctx = state.ctx) {
  const params = new URLSearchParams({view: ctx?.view || 'overview', ...extra});
  return '#/' + page + '?' + params;
}
function link(text, page, extra = {}, className = '') {
  const a = node('a', text, className); a.href = routeURL(page, extra); return a;
}
function readRoute() {
  const [path, query = ''] = location.hash.slice(1).split('?');
  const params = new URLSearchParams(query);
  const segments = (path || '/home').split('/').filter(Boolean);
  const requestedView = params.get('view') || 'overview';
  const ctx = {company_id: state.profile.company_id, role: state.profile.role, view: requestedView === 'actions' ? 'recommendations' : requestedView};
  if (!state.data.companies.some(x => x.id === ctx.company_id)) throw new Error('Компания в адресе не найдена.');
  if (!Object.hasOwn(state.data.roles, ctx.role) || !Object.hasOwn(state.data.views, ctx.view)) throw new Error('Неизвестная роль или разрез в адресе.');
  if (!['home', 'profile', 'feed', 'insight'].includes(segments[0]) || segments.length !== (segments[0] === 'insight' ? 2 : 1)) throw new Error('Страница не найдена.');
  if (segments[0] === 'insight') ctx.event_id = segments[1];
  return {page: segments[0], ctx};
}
function cancel() {
  state.epoch++; state.questionSeq++;
  state.pageController?.abort(); state.questionController?.abort(); state.card = null;
}
function showError(message, retry) {
  const box = $('error'); box.tabIndex = -1; box.replaceChildren(node('div', message)); box.hidden = false; box.focus();
  if (retry) { const b = node('button', 'Повторить'); b.onclick = retry; box.append(b); }
}
async function request(path, ctx, controller, payload) {
  if (api.mode === 'server') {
    const name = path === '/api/feed' ? 'feed' : path === '/api/insight' ? 'insight' : 'clarify';
    const result = await service(name, payload || ctx, controller, payload?.request_id);
    for (const key of Object.keys(ctx)) if (result.context?.[key] !== ctx[key]) throw new Error('Сервис вернул ответ для другого контекста.');
    return result;
  }
  const timeout = setTimeout(() => controller.abort('timeout'), 15000);
  try {
    const response = await fetch(path + (payload ? '' : '?' + new URLSearchParams(ctx)), {
      signal: controller.signal, ...(payload ? {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Demo-Client': 'd09-d10'}, body: JSON.stringify(payload)} : {})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || 'Ошибка сервиса.');
    for (const key of Object.keys(ctx)) if (result.context?.[key] !== ctx[key]) throw new Error('Сервис вернул ответ для другого контекста. Повторите запрос.');
    return result;
  } catch (e) {
    if (controller.signal.reason === 'timeout') throw new Error('Сервис не ответил за 15 секунд. Повторите запрос.');
    throw e;
  } finally { clearTimeout(timeout); }
}
function sourceLinks(sources = []) {
  const container = node('div');
  for (const source of sources) {
    const href = InsightsIntegration.sourceURL(source, api.mode === 'demo', location.origin);
    const label = source.title || 'Первоисточник';
    if (!href) { container.append(node('p', label + ' · ссылка недоступна', 'muted')); continue; }
    const a = node('a', label, 'source-link');
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.append(node('small', [source.date, Number.isInteger(source.page) && source.page > 0 ? 'стр. ' + source.page : null, 'новая вкладка'].filter(Boolean).join(' · ')));
    container.append(a);
    if (source.excerpt) container.append(node('blockquote', source.excerpt));
  }
  return container;
}
function claimEvidence(part, sources) {
  const box = sourceLinks(sources.filter(s => (part.source_ids || []).includes(s.id)));
  if (part.claim_id) box.dataset.claimId = part.claim_id;
  if (part.feature_refs?.length) box.append(node('p', 'Использованные признаки: ' + part.feature_refs.join(', '), 'muted'));
  return box;
}
function guardAccess(error) {
  if (![401, 403].includes(error.status)) return;
  cancel(); pendingOperations.clear(); state.data = null; state.profile = null; state.ctx = null; state.preferences = {};
  $('page').replaceChildren(); $('profile-link').textContent = 'Доступ не подтверждён';
}
async function service(name, input, controller, requestId) {
  const operationKey = JSON.stringify([name, input]);
  try { if (pendingOperations.has(operationKey)) { const result = await pendingOperations.get(operationKey)(controller.signal); pendingOperations.delete(operationKey); return result; } return await api.call(name, input, {signal:controller.signal, requestId, onProgress: () => status('Анализ выполняется. Можно перейти на другой экран.')}); }
  catch (e) { if (e.resume) pendingOperations.set(operationKey,e.resume); guardAccess(e); throw e; }
}
async function persistServer(key, value, button, message, target = {}) {
  const epoch = state.epoch, context = {...(target.context || state.ctx)}; button.disabled = true;
  message.textContent = 'Сохраняем…';
  try {
    await service('savePreference', {context, key, value, insight_id:target.insight_id || state.card?.insight_id}, new AbortController(), crypto.randomUUID());
    if (epoch !== state.epoch) return false;
    state.preferences = {...state.preferences, [key]:{...state.preferences[key], ...value}};
    message.textContent = 'Сохранено на сервере.'; return true;
  } catch (e) { if (epoch === state.epoch || [401,403].includes(e.status)) { message.textContent = 'Изменение не сохранено. ' + e.message; showError(message.textContent); } return false; }
  finally { button.disabled = false; }
}
function heading(title, lead) {
  $('page').append(node('h1', title), node('p', lead, 'lead'));
  document.title = title + ' · учебный Альфа-Бизнес';
}
function renderHome() {
  heading('Обзор бизнеса', 'Ключевые события и понятные ориентиры для вашей компании.');
  const hero = node('section', '', 'hero');
  hero.append(node('p', 'Аналитика для вашей компании', 'eyebrow'), node('h2', 'Что меняется вокруг вашего бизнеса?'),
    node('p', 'Узнайте, какие события требуют внимания, на чём основаны выводы и что стоит проверить.'), link('Открыть бизнес-события →', 'feed', {}, 'primary-link'));
  const grid = node('div', '', 'grid');
  for (const [title, body] of [['От факта к действию', 'Посмотрите, что произошло, как событие может повлиять на компанию, какие есть риски и что проверить дальше. Основания и источники доступны в каждом разделе.']]) {
    const p = node('section', '', 'panel'); p.append(node('h2', title), node('p', body, 'muted')); grid.append(p);
  }
  $('page').append(hero, grid); status('Главная страница');
}
function renderProfile() {
  heading('Профиль', 'Данные пользователя и компании для персональной аналитики.');
  const grid = node('div', '', 'profile-grid');
  const details = node('section', '', 'panel'); details.append(node('h2', 'Данные профиля'));
  const form = node('form'); form.id = 'profile-form';
  const nameLabel = node('label', 'Имя'); nameLabel.htmlFor = 'profile-name';
  const nameInput = node('input'); nameInput.id = 'profile-name'; nameInput.value = state.profile.name; nameInput.maxLength = 100; nameInput.required = true; nameInput.autocomplete = 'name'; nameInput.readOnly = api.mode === 'server';
  const companyLabel = node('label', 'Компания'); companyLabel.htmlFor = 'profile-company';
  const companySelect = node('select'); companySelect.id = 'profile-company';
  for (const company of state.data.companies) { const option = node('option', company.name); option.value = company.id; companySelect.append(option); }
  companySelect.value = state.profile.company_id;
  const companyInfo = node('p', '', 'muted'); companyInfo.id = 'profile-company-info';
  const describeCompany = () => { const company = state.data.companies.find(c => c.id === companySelect.value); companyInfo.textContent = 'Отрасль: ' + (company.sector || 'не указана') + (api.mode === 'demo' ? '. Данные компании — демонстрационные.' : ''); };
  describeCompany();
  const roleLabel = node('label', 'Должность'); roleLabel.htmlFor = 'profile-role';
  const roleSelect = node('select'); roleSelect.id = 'profile-role';
  for (const [value, text] of Object.entries(state.data.roles)) { const option = node('option', text); option.value = value; roleSelect.append(option); }
  roleSelect.value = state.profile.role;
  const restrictRoles = () => {
    const allowed = state.data.companies.find(c=>c.id===companySelect.value)?.roles;
    for (const option of roleSelect.children) option.disabled = Array.isArray(allowed) && !allowed.includes(option.value);
    if (Array.isArray(allowed) && !allowed.includes(roleSelect.value)) roleSelect.value = allowed[0] || '';
  };
  companySelect.onchange = () => { describeCompany(); restrictRoles(); }; restrictRoles();
  const save = node('button', 'Сохранить профиль', 'primary'); save.type = 'submit';
  form.append(nameLabel, nameInput, companyLabel, companySelect, companyInfo, roleLabel, roleSelect, node('p', api.mode === 'demo' ? 'Настройки сохраняются в этом браузере и используются в ленте и карточках.' : 'Компания и доступные роли получены из серверной сессии.', 'muted'), save);
  form.onsubmit = async event => {
    event.preventDefault(); const name = nameInput.value.trim(); if (!name) {nameInput.setCustomValidity('Введите имя.'); nameInput.reportValidity(); return;}
    if (!roleSelect.value) { showError('Для выбранной компании нет доступной роли.'); return; }
    const profile = {name, company_id: companySelect.value, role: roleSelect.value};
    if (api.mode === 'demo' && !saveStored(PROFILE_KEY, profile)) { showError('Не удалось сохранить профиль в этом браузере.'); return; }
    state.profile = profile; await renderRoute(); status('Профиль сохранён. Аналитика использует данные из профиля.');
  };
  nameInput.oninput = () => nameInput.setCustomValidity('');
  details.append(form);
  if (api.mode === 'server') {
    const session = node('section', '', 'panel'); session.append(node('h2', 'Сессия интернет-банка'), node('p', 'Доступ проверяет сервер. Выбор компании не предоставляет дополнительных прав.'));
    grid.append(details, session); $('page').append(grid); status('Профиль пользователя'); return;
  }
  const auth = node('section', '', 'panel'); auth.append(node('h2', 'Вход в аккаунт'), node('p', 'Форма будущего входа по логину и паролю. Сейчас доступен демонстрационный профиль.', 'muted'));
  const loginForm = node('form'); loginForm.id = 'login-form';
  const loginLabel = node('label', 'Логин'); loginLabel.htmlFor = 'login';
  const login = node('input'); login.id = 'login'; login.name = 'username'; login.autocomplete = 'username'; login.required = true; login.maxLength = 100; login.placeholder = 'Ваш логин';
  const passwordLabel = node('label', 'Пароль'); passwordLabel.htmlFor = 'password';
  const password = node('input'); password.id = 'password'; password.name = 'password'; password.type = 'password'; password.autocomplete = 'current-password'; password.required = true; password.maxLength = 256;
  const enter = node('button', 'Войти', 'primary'); enter.type = 'submit';
  const message = node('p', '', 'muted'); message.id = 'login-status'; message.setAttribute('role', 'status');
  loginForm.onsubmit = event => {event.preventDefault(); password.value = ''; message.textContent = 'Вход пока не подключён. Логин и пароль не отправлены и не сохранены. Продолжайте работу с демопрофилем.';};
  loginForm.append(loginLabel, login, passwordLabel, password, enter, message); auth.append(loginForm); grid.append(details, auth); $('page').append(grid); status('Профиль пользователя');
}
function feedbackPanel(card) {
  if (api.mode === 'server') return serverFeedbackPanel(card);
  const panel = node('section', '', 'panel feedback-panel'); panel.append(node('h2', 'Была ли аналитика полезна?'));
  const actions = node('div', '', 'question-actions'); const message = node('p', '', 'muted'); message.setAttribute('role', 'status');
  const key = feedbackKey(card); const current = state.preferences[key]?.feedback;
  for (const [value, text] of [['useful', 'Полезно'], ['not_useful', 'Не полезно']]) {
    const button = node('button', text); button.setAttribute('aria-pressed', String(value === current));
    button.onclick = () => {
      if (!state.card || feedbackKey(state.card) !== key) return;
      if (value === 'not_useful') { reason.hidden = false; saveReason.hidden = false; message.textContent = 'Если хотите, укажите причину и сохраните оценку.'; return; }
      const updated = {...state.preferences, [key]: {feedback: value, reason: null, ...(state.preferences[key]?.reason || state.preferences[key]?.previousNegativeReason ? {previousNegativeReason: state.preferences[key].reason || state.preferences[key].previousNegativeReason} : {})}};
      if (!saveStored(PREFERENCES_KEY, updated)) { message.textContent = 'Не удалось сохранить оценку в этом браузере.'; return; }
      state.preferences = updated; for (const item of actions.children) item.setAttribute('aria-pressed', String(item === button));
      reason.hidden = true; saveReason.hidden = true; message.textContent = 'Спасибо! Оценка сохранена в этом браузере.';
    }; actions.append(button);
  }
  const reason = node('select'); reason.setAttribute('aria-label', 'Причина оценки'); reason.hidden = true;
  for (const [value, text] of [['', 'Можно выбрать причину'], ['company', 'Не относится к компании'], ['basis', 'Неясно основание'], ['error', 'Есть ошибка'], ['decision', 'Не помогает принять решение'], ['other', 'Другое']]) { const option = node('option', text); option.value = value; reason.append(option); }
  reason.value = state.preferences[key]?.feedback === 'not_useful' ? (state.preferences[key]?.reason || '') : (state.preferences[key]?.previousNegativeReason || '');
  const saveReason = node('button', 'Сохранить оценку'); saveReason.hidden = true; saveReason.onclick = () => {
    if (!state.card || feedbackKey(state.card) !== key) return;
    const updated = {...state.preferences, [key]: {feedback: 'not_useful', reason: reason.value || state.preferences[key]?.reason || state.preferences[key]?.previousNegativeReason || null, ...(reason.value || state.preferences[key]?.reason || state.preferences[key]?.previousNegativeReason ? {previousNegativeReason: reason.value || state.preferences[key].reason || state.preferences[key].previousNegativeReason} : {})}};
    if (!saveStored(PREFERENCES_KEY, updated)) { message.textContent = 'Не удалось сохранить оценку в этом браузере.'; return; }
    state.preferences = updated; for (const item of actions.children) item.setAttribute('aria-pressed', String(item.textContent === 'Не полезно'));
    saveReason.hidden = true; reason.hidden = true; message.textContent = 'Спасибо! Оценка сохранена в этом браузере.';
  };
  message.textContent = current ? 'Ваша оценка этой версии сохранена в этом браузере.' : 'Оценка сохраняется локально для текущей компании, должности и версии анализа.';
  panel.append(actions, reason, saveReason, message); return panel;
}

function serverFeedbackPanel(card) {
  const panel = node('section', '', 'panel feedback-panel'); panel.append(node('h2', 'Была ли аналитика полезна?'));
  const message = node('p', '', 'muted'); message.setAttribute('role','status');
  const reason = node('select'); reason.setAttribute('aria-label','Причина отрицательной оценки (необязательно)');
  for (const [value, text] of [['','Можно выбрать причину'],['company','Не относится к компании'],['basis','Неясно основание'],['error','Есть ошибка'],['decision','Не помогает принять решение'],['other','Другое']]) { const option=node('option',text); option.value=value; reason.append(option); }
  const key=feedbackKey(card), epoch=state.epoch, buttons=[];
  reason.value=state.preferences[key]?.reason || '';
  for (const [value,text] of [['useful','Полезно'],['not_useful','Не полезно']]) {
    const button=node('button',text); buttons.push(button); button.setAttribute('aria-pressed',String(state.preferences[key]?.feedback===value));
    button.onclick=async()=>{
      if (epoch!==state.epoch || !state.card || feedbackKey(state.card)!==key || buttons.some(b=>b.disabled)) return;
      buttons.forEach(b=>b.disabled=true);
      const ok=await persistServer(key,{feedback:value,reason:value==='not_useful' ? reason.value || null : null},button,message);
      buttons.forEach(b=>b.disabled=false);
      if (ok) buttons.forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    }; panel.append(button);
  }
  panel.append(reason,message); return panel;
}
function renderFeed(result) {
  heading('Бизнес-события', 'События и разборы для компании из профиля. Изменить компанию или должность можно в разделе «Профиль».');
  if (!result.items.length) {
    const box = node('section', '', 'empty'); box.append(node('h2', 'Пока нет подготовленных событий'), node('p', result.empty_reason || 'Проверьте данные компании в профиле. Отсутствие событий не означает отсутствие рисков.'), link('Вернуться на главную', 'home'));
    $('page').append(box); status('Событий пока нет'); return;
  }
  const hidden = result.items.filter(event => state.preferences[preferenceKey(event.id)]?.hidden);
  const visible = result.items.filter(event => !state.preferences[preferenceKey(event.id)]?.hidden);
  if (hidden.length) {
    const hiddenBar = node('div', '', 'hidden-bar');
    hiddenBar.append(node('span', 'Скрыто событий: ' + hidden.length));
    const restore = node('button', 'Показать скрытые');
    const restoreContext = JSON.stringify(state.ctx);
    restore.onclick = async () => {
      if (JSON.stringify(state.ctx) !== restoreContext) return;
      if (api.mode === 'server') {
        const epoch=state.epoch;
        for (const event of hidden) { if (epoch!==state.epoch) return; if (!await persistServer(preferenceKey(event.id), {hidden:false}, restore, hiddenBar, {context:{...state.ctx,event_id:event.id},insight_id:event.insight_id})) return; }
        await renderRoute(); return;
      }
      const updated = {...state.preferences};
      for (const event of hidden) { const key = preferenceKey(event.id); updated[key] = {...updated[key], hidden: false}; }
      if (!saveStored(PREFERENCES_KEY, updated)) { showError('Не удалось восстановить события в локальном хранилище.'); return; }
      state.preferences = updated; renderRoute();
    };
    hiddenBar.append(restore); $('page').append(hiddenBar);
  }
  const grid = node('div', '', 'grid');
  for (const event of visible) {
    const card = node('article', '', 'event-card'); const meta = node('div', '', 'meta');
    meta.append(node('span', event.category), node('span', event.date));
    const actions = node('div', '', 'event-actions');
    const hide = node('button', 'Скрыть'); hide.setAttribute('aria-label', 'Скрыть событие «' + event.title + '»');
    const hideKey = preferenceKey(event.id), hideContext = `${state.profile.company_id}:${state.profile.role}`;
    hide.onclick = async () => { if (`${state.profile.company_id}:${state.profile.role}` !== hideContext) return; if (api.mode === 'server') { const message=node('p','','muted'); card.append(message); if (await persistServer(hideKey,{hidden:true},hide,message,{context:{...state.ctx,event_id:event.id},insight_id:event.insight_id})) await renderRoute(); return; } const updated = {...state.preferences, [hideKey]: {...state.preferences[hideKey], hidden: true}}; if (!saveStored(PREFERENCES_KEY, updated)) { showError('Не удалось сохранить скрытие в этом браузере.'); return; } state.preferences = updated; renderRoute(); };
    actions.append(link('Разобраться в событии →', 'insight/' + event.id), hide);
    card.append(meta, node('h2', event.title), node('span', 'Требует проверки данных', 'badge'), node('p', event.why), actions); grid.append(card);
  }
  if (!visible.length) grid.append(node('p', 'Все события скрыты. Их можно вернуть кнопкой «Показать скрытые».', 'muted'));
  $('page').append(grid); status('Показано событий: ' + visible.length + ' · дата анализа ' + result.as_of);
}

function renderCard(card) {
  state.card = card;
  if (card.outcome && card.outcome !== 'COMPLETE') {
    const warning=node('section','','limitations'); warning.setAttribute('role','status');
    warning.append(node('h2', InsightsIntegration.outcomes[card.outcome] || 'Статус результата не распознан'), node('p',card.outcome_message || 'Учитывайте ограничения результата.'));
    $('page').append(warning);
    if (!['LIMITED'].includes(card.outcome)) { $('page').append(link('Вернуться к событиям','feed')); status(warning.textContent); return; }
  }
  $('page').append(link('← Все бизнес-события', 'feed', {}, 'breadcrumb'));
  const company = state.data.companies.find(c => c.id === state.ctx.company_id);
  heading(card.title, `Разбор для ${company?.name || 'компании'} · ${state.data.roles[state.ctx.role]}. Изменить контекст можно в Профиле.`);
  const toolbar = node('div', '', 'toolbar'); toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', 'Разрез анализа');
  for (const [key, name] of Object.entries(state.data.views)) {
    const b = node('button', name); b.setAttribute('aria-pressed', String(key === state.ctx.view));
    b.onclick = () => { location.hash = routeURL('insight/' + state.ctx.event_id, {view: key}); }; toolbar.append(b);
  }
  $('page').append(toolbar);
  const layout = node('div', '', 'card-layout'); const content = node('div'); const blocks = node('article', '', 'panel thematic-analysis');
  const fact = card.blocks.find(b => b.kind === 'fact');
  const factSection = node('section', '', 'block fact'); factSection.append(node('h2', 'Что произошло', 'block-heading'));
  if (fact && (!fact.claims || fact.claims.length)) for (const claim of fact.claims || [fact]) factSection.append(node('span', claim.certainty === 'fact' ? 'Факт события' : certaintyLabels[claim.certainty] || 'Тип утверждения не указан', 'tag'), node('p', claim.text), claimEvidence(claim, card.sources));
  else factSection.append(node('p', 'Факт события не предоставлен.', 'muted'));
  blocks.append(factSection);
  const themeMap = {consequences: 'Влияние на компанию', risks: 'Риски и неопределённость', recommendations: 'Что проверить дальше'};
  const origins = {open: 'Вывод AI по внешним источникам', bank: api.mode === 'demo' ? 'Оценка банка · в этой демонстрации учебная' : 'Оценка банка по указанному материалу', ai: 'Вывод AI для компании · не официальная позиция банка'};
  const bankBlock = card.blocks.find(b => b.kind === 'bank');
  const bankUnavailable = !bankBlock || !bankBlock.sections?.some(part => part.certainty !== 'unavailable');
  if (bankUnavailable) blocks.append(node('p', api.mode === 'demo' ? 'Для этого события оценка банка недоступна: в демонстрационном наборе нет релевантного материала.' : 'Для этого события оценка банка недоступна: подтверждающий материал не предоставлен.', 'bank-unavailable'));
  for (const kind of ['consequences', 'risks', 'recommendations']) {
    if (state.ctx.view !== 'overview' && state.ctx.view !== kind) continue;
    const theme = node('section', '', 'theme-section'); theme.append(node('h2', themeMap[kind], 'block-heading'));
    const aiParts = card.blocks.find(b => b.kind === 'ai')?.sections?.filter(section => section.kind === kind) || [];
    if (aiParts.length) { theme.append(node('p', origins.ai, 'origin-note ai-origin')); for (const aiPart of aiParts) {
      if (aiPart.label) theme.append(node('p',aiPart.label,'muted'));
      theme.append(node('p', aiPart.text, 'analysis-copy'), node('span', certaintyLabels[aiPart.certainty] || 'Тип утверждения не указан', 'tag'), claimEvidence(aiPart, card.sources));
    } }
    else theme.append(node('p', origins.ai, 'origin-note ai-origin'), node('p', 'Вывод для компании по этой теме недоступен: раздел не предоставлен.', 'muted'), node('span', certaintyLabels.unavailable, 'tag'));
    const basis = node('details', '', 'basis-details'); basis.append(node('summary', 'На чём основан вывод'));
    for (const kindOrigin of ['open', 'bank']) {
      const block = card.blocks.find(b => b.kind === kindOrigin); const parts = block?.sections?.filter(section => section.kind === kind) || [];
      const sourceOrigin = node('section', '', 'source-origin ' + kindOrigin);
      sourceOrigin.append(node('p', origins[kindOrigin], 'origin-note'));
      if (parts.length) {
        for (const part of parts) {
          if (part.label) sourceOrigin.append(node('p',part.label,'muted'));
          sourceOrigin.append(node('p', part.text, 'origin-copy'));
          sourceOrigin.append(node('span', certaintyLabels[part.certainty] || 'Тип утверждения не указан', 'tag'), claimEvidence(part, card.sources));
        }
      } else {
        sourceOrigin.append(node('p', 'Раздел по этой теме не предоставлен.', 'muted'), node('span', certaintyLabels.unavailable, 'tag'));
      }
      basis.append(sourceOrigin);
    }
    theme.append(basis);
    blocks.append(theme);
  }
  if (card.missing_data.length) {
    const warning = node('div', '', 'limitations'); warning.append(node('strong', 'Границы вывода'));
    for (const text of card.missing_data) warning.append(node('p', text)); blocks.append(warning);
  }
  content.append(blocks, questionPanel(), feedbackPanel(card));
  const aside = node('aside', '', 'panel aside-panel'); aside.append(node('h2', 'Основания и источники'), node('p', api.mode === 'demo' ? 'Синтетические материалы для проверки интерфейса. Оригинал открывается в новой вкладке.' : 'Первоисточники выводов. Оригинал открывается в новой вкладке.'), sourceLinks(card.sources), node('p', 'Вывод не заменяет проверку договоров и фактических показателей.'));
  const disclaimer = node('div', '', 'investment-note'); disclaimer.append(node('strong', 'Не является инвестрекомендацией'), node('p', card.disclaimer)); aside.append(disclaimer);
  layout.append(content, aside); $('page').append(layout); status(card.outcome === 'LIMITED' || card.missing_data.length ? 'Карточка готова · есть ограничения данных' : 'Карточка готова');
}
function questionPanel() {
  const panel = node('section', '', 'panel question-panel'); panel.append(node('h2', 'Уточнить по этому событию'));
  const quick = node('div', '', 'question-actions');
  for (const q of ['Почему это важно?', 'Что проверить?', 'Сколько составит эффект?']) {
    const b = node('button', q); b.type = 'button'; b.onclick = () => { $('question').value = q; ask(q); }; quick.append(b);
  }
  const form = node('form'); form.id = 'question-form';
  const label = node('label', 'Ваш вопрос'); label.htmlFor = 'question';
  const input = node('textarea'); input.id = 'question'; input.maxLength = 1000; input.required = true; input.placeholder = 'Например: какие данные нужно уточнить?';
  const row = node('div', '', 'submit-row'); const submit = node('button', 'Получить ответ', 'primary'); submit.id = 'submit'; submit.type = 'submit';
  row.append(node('span', 'До 1000 символов · только текущая карточка'), submit);
  form.append(label, input, row); form.onsubmit = e => { e.preventDefault(); if (input.value.trim()) ask(input.value.trim()); };
  const reply = node('div'); reply.id = 'reply'; reply.setAttribute('aria-live', 'polite');
  panel.append(quick, form, reply); return panel;
}
async function ask(question, requestId = crypto.randomUUID()) {
  if (!state.card) return;
  state.questionController?.abort(); const controller = new AbortController(); state.questionController = controller;
  const epoch = state.epoch, seq = ++state.questionSeq, ctx = {...state.ctx};
  const payload = {...ctx, insight_id: state.card.insight_id, question, request_id: requestId};
  const target = $('reply'); target.replaceChildren(node('p', 'Готовим уточнение…')); target.setAttribute('aria-busy', 'true');
  $('submit').disabled = true; $('error').hidden = true; status('Готовим уточнение в текущем контексте…');
  try {
    const result = await request('/api/clarifications', ctx, controller, payload);
    if (epoch !== state.epoch || seq !== state.questionSeq) return;
    if (result.request_id !== payload.request_id || result.insight_id !== payload.insight_id) throw new Error('Ответ не соответствует запросу или версии карточки.');
    target.replaceChildren(); target.className = 'reply';
    target.append(node('strong', InsightsIntegration.outcomes[result.outcome] || (result.status === 'no_answer' ? 'Достоверный ответ недоступен' : 'Уточнение · есть ограничения')), node('p', question, 'muted'), node('p', result.text), sourceLinks(result.sources));
    if (result.claims?.length) {
      const evidence=node('details'); evidence.append(node('summary','Основания ответа'));
      for (const claim of result.claims) evidence.append(node('p',claim.text),node('span',certaintyLabels[claim.certainty] || 'Тип утверждения не указан','tag'),claimEvidence(claim,result.sources));
      target.append(evidence);
    }
    if (result.missing_data?.length) {
      const limits=node('div','','limitations'); limits.append(node('strong','Границы ответа'));
      for (const text of result.missing_data) limits.append(node('p',text)); target.append(limits);
    }
    status(result.status === 'no_answer' ? 'Недостаточно оснований для ответа' : 'Уточнение готово');
  } catch (e) {
    if (((epoch !== state.epoch || seq !== state.questionSeq) && ![401,403].includes(e.status)) || e.name === 'AbortError') return;
    target.replaceChildren(); showError(e.message, state.data ? () => ask(question, requestId) : init); status('Не удалось получить уточнение');
  } finally {
    if (epoch === state.epoch && seq === state.questionSeq) { target.setAttribute('aria-busy', 'false'); $('submit').disabled = false; }
  }
}
async function renderRoute() {
  cancel(); const epoch = state.epoch; $('error').hidden = true; $('page').replaceChildren();
  try {
    const route = readRoute(); state.ctx = route.ctx;
    // Company and role come from the profile; old URL overrides are intentionally ignored.
    history.replaceState(null, '', routeURL(route.page === 'insight' ? 'insight/' + route.ctx.event_id : route.page));
    $('profile-link').textContent = state.profile.name + ' · ' + state.data.companies.find(x => x.id === state.profile.company_id).name + ' · ' + state.data.roles[state.profile.role];
    $('profile-link').href = routeURL('profile');
    document.querySelectorAll('[data-nav]').forEach(a => {
      a.href = routeURL(a.dataset.nav);
      if (a.dataset.nav === (route.page === 'insight' ? 'feed' : route.page)) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    document.querySelector('.brand').href = routeURL('home');
    $('main').focus({preventScroll: true});
    if (route.page === 'home') { renderHome(); return; }
    if (route.page === 'profile') { renderProfile(); return; }
    $('page').setAttribute('aria-busy', 'true'); status('Загружаем аналитику…');
    $('page').append(node('div', 'Загружаем данные выбранной компании…', 'skeleton'));
    const controller = new AbortController(); state.pageController = controller;
    if (api.mode === 'server') {
      const preferences = await service('preferences', {...state.ctx}, controller);
      if (epoch !== state.epoch) return;
      state.preferences = normalizePreferences(preferences);
    }
    const ctx = route.page === 'feed' ? {company_id: state.ctx.company_id, role: state.ctx.role} : {...state.ctx, view: 'overview'};
    const result = await request(route.page === 'feed' ? '/api/feed' : '/api/insight', ctx, controller);
    if (epoch !== state.epoch) return;
    $('page').replaceChildren(); if (route.page === 'feed') renderFeed(result); else { renderCard(result);
      if (api.mode === 'server') service('opened', {context:{...state.ctx}, insight_id:result.insight_id}, controller, crypto.randomUUID()).catch(e=>{if(epoch===state.epoch || [401,403].includes(e.status)) showError('Не удалось сохранить открытие. ' + e.message);});
    }
  } catch (e) {
    if ((epoch !== state.epoch && ![401,403].includes(e.status)) || e.name === 'AbortError') return;
    $('page').replaceChildren(); showError(e.message, state.data ? renderRoute : init); status('Не удалось открыть страницу');
    const a = node('a', 'Вернуться на главную'); a.href = '#/home'; $('page').append(a);
  } finally { if (epoch === state.epoch) $('page').setAttribute('aria-busy', 'false'); }
}
async function init() {
  try {
    api = InsightsIntegration.createClient(window.INSIGHTS_CONFIG || {});
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 15000);
    let result;
    try { if (api.mode === 'server') result = await service('bootstrap', {}, controller); else { const response = await fetch('/api/bootstrap', {signal: controller.signal}); if (!response.ok) throw new Error('Сервис недоступен.'); result = await response.json(); } }
    finally { clearTimeout(timeout); }
    if (!result.companies?.length || !result.profile || !result.roles) throw new Error('В сессии нет доступного профиля компании.');
    state.data = result;
    const badge=document.querySelector('.demo-pill'); if (badge) badge.textContent=api.mode==='demo' ? 'Учебные данные' : 'Серверный режим';
    const stored = api.mode === 'demo' ? readStored(PROFILE_KEY, result.profile) : result.profile;
    const valid = stored && typeof stored.name === 'string' && stored.name.trim().length > 0 && stored.name.length <= 100 && result.companies.some(c => c.id === stored.company_id) && Object.hasOwn(result.roles, stored.role);
    state.profile = valid ? {name: stored.name, company_id: stored.company_id, role: stored.role} : {...result.profile};
    state.preferences = api.mode === 'demo' ? normalizePreferences(readStored(PREFERENCES_KEY, {})) : {};
    $('notice').textContent = result.notice;
    await renderRoute();
  } catch (e) { showError('Не удалось подключить сервис. ' + e.message, init); status('Ошибка подключения'); $('page').setAttribute('aria-busy', 'false'); }
}
window.addEventListener('hashchange', () => { if (state.data) renderRoute(); });
document.querySelector('.skip').addEventListener('click', event => { event.preventDefault(); $('main').focus(); });
init();
