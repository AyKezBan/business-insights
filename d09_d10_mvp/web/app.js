'use strict';
const $ = id => document.getElementById(id);
const state = {data: null, ctx: null, epoch: 0, pageController: null, questionController: null, card: null, questionSeq: 0, profile: null, preferences: {}};
const node = (tag, text = '', className = '') => {
  const n = document.createElement(tag); n.textContent = text; n.className = className; return n;
};
const status = text => { $('status').textContent = text; };
const labels = {fact: 'Факт события', open: 'Аналитика по открытым источникам', bank: 'Аналитика от Альфа-банка', ai: 'Вывод для компании'};
const certaintyLabels = {fact: 'Факт', inference: 'Вывод модели по источникам', hypothesis: 'Гипотеза · требует проверки', assessment: 'Оценка в аналитическом материале', recommendation: 'Рекомендация · требует проверки данных', unavailable: 'Нет данных'};
const PROFILE_KEY = 'd09-d10-profile-v1';
const PREFERENCES_KEY = 'd09-d10-preferences-v1';
function readStored(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; } }
function saveStored(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
function preferenceKey(eventId) { return state.profile.company_id + ':' + state.profile.role + ':' + eventId; }
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
  const box = $('error'); box.replaceChildren(node('div', message)); box.hidden = false;
  if (retry) { const b = node('button', 'Повторить'); b.onclick = retry; box.append(b); }
}
async function request(path, ctx, controller, payload) {
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
function sourceLinks(sources) {
  const container = node('div');
  for (const source of sources) {
    // Only server-owned source identifiers; no model-generated HTML or URLs.
    if (!/^[a-z0-9-]+$/.test(source.id)) continue;
    const a = node('a', source.title, 'source-link');
    a.href = '/sources/' + encodeURIComponent(source.id); a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.append(node('small', `${source.date} · стр. ${source.page} · новая вкладка`)); container.append(a);
  }
  return container;
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
  for (const [title, body] of [['От факта к действию', 'Сравните аналитику по открытым источникам, материалы банка и вывод для компании. Посмотрите последствия, риски и рекомендации.']]) {
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
  const nameInput = node('input'); nameInput.id = 'profile-name'; nameInput.value = state.profile.name; nameInput.maxLength = 100; nameInput.required = true; nameInput.autocomplete = 'name';
  const companyLabel = node('label', 'Компания'); companyLabel.htmlFor = 'profile-company';
  const companySelect = node('select'); companySelect.id = 'profile-company';
  for (const company of state.data.companies) { const option = node('option', company.name); option.value = company.id; companySelect.append(option); }
  companySelect.value = state.profile.company_id;
  const companyInfo = node('p', '', 'muted'); companyInfo.id = 'profile-company-info';
  const describeCompany = () => { const company = state.data.companies.find(c => c.id === companySelect.value); companyInfo.textContent = 'Отрасль: ' + company.sector + '. Данные компании — демонстрационные.'; };
  companySelect.onchange = describeCompany; describeCompany();
  const roleLabel = node('label', 'Должность'); roleLabel.htmlFor = 'profile-role';
  const roleSelect = node('select'); roleSelect.id = 'profile-role';
  for (const [value, text] of Object.entries(state.data.roles)) { const option = node('option', text); option.value = value; roleSelect.append(option); }
  roleSelect.value = state.profile.role;
  const save = node('button', 'Сохранить профиль', 'primary'); save.type = 'submit';
  form.append(nameLabel, nameInput, companyLabel, companySelect, companyInfo, roleLabel, roleSelect, node('p', 'Настройки сохраняются в этом браузере и используются в ленте и карточках.', 'muted'), save);
  form.onsubmit = async event => {
    event.preventDefault(); const name = nameInput.value.trim(); if (!name) {nameInput.setCustomValidity('Введите имя.'); nameInput.reportValidity(); return;}
    const profile = {name, company_id: companySelect.value, role: roleSelect.value};
    if (!saveStored(PROFILE_KEY, profile)) { showError('Не удалось сохранить профиль в этом браузере.'); return; }
    state.profile = profile; await renderRoute(); status('Профиль сохранён. Аналитика использует данные из профиля.');
  };
  nameInput.oninput = () => nameInput.setCustomValidity('');
  details.append(form);
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
  const panel = node('section', '', 'panel feedback-panel'); panel.append(node('h2', 'Была ли аналитика полезна?'));
  const actions = node('div', '', 'question-actions'); const message = node('p', '', 'muted'); message.setAttribute('role', 'status');
  const eventId = card.context.event_id; const current = state.preferences[preferenceKey(eventId)]?.feedback;
  for (const [value, text] of [['useful', 'Полезно'], ['not_useful', 'Не полезно']]) {
    const button = node('button', text); button.setAttribute('aria-pressed', String(value === current));
    button.onclick = () => {
      if (!setPreference(eventId, {feedback: value})) return;
      for (const item of actions.children) item.setAttribute('aria-pressed', String(item === button));
      message.textContent = 'Спасибо! Оценка сохранена в этом браузере.';
    }; actions.append(button);
  }
  message.textContent = current ? 'Ваша оценка сохранена в этом браузере.' : 'Оценка сохраняется локально для текущей компании и должности.';
  panel.append(actions, message); return panel;
}

function renderFeed(result) {
  heading('Бизнес-события', 'Подготовленная аналитика с учётом компании и должности из вашего профиля.');
  if (!result.items.length) {
    const box = node('section', '', 'empty'); box.append(node('h2', 'Пока нет подготовленных событий'), node('p', 'Проверьте данные компании в профиле. Отсутствие событий не означает отсутствие рисков.'), link('Вернуться на главную', 'home'));
    $('page').append(box); status('Событий пока нет'); return;
  }
  const hidden = result.items.filter(event => state.preferences[preferenceKey(event.id)]?.hidden);
  const visible = result.items.filter(event => !state.preferences[preferenceKey(event.id)]?.hidden);
  if (hidden.length) {
    const hiddenBar = node('div', '', 'hidden-bar');
    hiddenBar.append(node('span', 'Скрыто событий: ' + hidden.length));
    const restore = node('button', 'Показать скрытые');
    restore.onclick = () => {
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
    hide.onclick = () => { if (setPreference(event.id, {hidden: true})) renderRoute(); };
    actions.append(link('Разобраться в событии →', 'insight/' + event.id), hide);
    card.append(meta, node('h2', event.title), node('span', 'Требует проверки данных', 'badge'), node('p', event.why), actions); grid.append(card);
  }
  if (!visible.length) grid.append(node('p', 'Все события скрыты. Их можно вернуть кнопкой «Показать скрытые».', 'muted'));
  $('page').append(grid); status('Показано событий: ' + visible.length + ' · дата анализа ' + result.as_of);
}

function renderCard(card) {
  state.card = card;
  $('page').append(link('← Все бизнес-события', 'feed', {}, 'breadcrumb'));
  heading(card.title, 'Что произошло, как это может повлиять на компанию и какие данные ещё нужны.');
  const toolbar = node('div', '', 'toolbar'); toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', 'Разрез анализа');
  for (const [key, name] of Object.entries(state.data.views)) {
    const b = node('button', name); b.setAttribute('aria-pressed', String(key === state.ctx.view));
    b.onclick = () => { location.hash = routeURL('insight/' + state.ctx.event_id, {view: key}); }; toolbar.append(b);
  }
  $('page').append(toolbar);
  const layout = node('div', '', 'card-layout'); const content = node('div'); const blocks = node('article', '', 'panel');
  for (const block of card.blocks) {
    const section = node('section', '', 'block ' + block.kind);
    section.append(node('h2', labels[block.kind] || 'Аналитика', 'block-heading'));
    if (block.kind === 'fact') {
      section.append(node('span', 'Факт из источника', 'tag'), node('p', block.text), sourceLinks(card.sources.filter(s => block.source_ids.includes(s.id))));
    } else {
      const origin = block.kind === 'open' ? 'Вывод модели по открытым источникам' : block.kind === 'bank' ? 'Основание — материалы банка; в демо использованы синтетические примеры' : 'Вывод модели для компании · не официальная позиция банка';
      section.append(node('p', origin, 'origin-note'));
      for (const part of block.sections) {
        const subsection = node('section', '', 'analysis-part');
        subsection.append(node('h3', state.data.views[part.kind]), node('span', certaintyLabels[part.certainty] || 'Требует проверки', 'tag'), node('p', part.text), sourceLinks(card.sources.filter(s => part.source_ids.includes(s.id))));
        section.append(subsection);
      }
    }
    blocks.append(section);
  }
  if (card.missing_data.length) {
    const warning = node('div', '', 'limitations'); warning.append(node('strong', 'Границы вывода'));
    for (const text of card.missing_data) warning.append(node('p', text)); blocks.append(warning);
  }
  content.append(blocks, questionPanel(), feedbackPanel(card));
  const aside = node('aside', '', 'panel aside-panel'); aside.append(node('h2', 'Основания и источники'), node('p', 'Синтетические материалы для проверки интерфейса. Оригинал открывается в новой вкладке.'), sourceLinks(card.sources), node('p', 'Вывод не заменяет проверку договоров и фактических показателей.'));
  const disclaimer = node('div', '', 'investment-note'); disclaimer.append(node('strong', 'Не является инвестрекомендацией'), node('p', card.disclaimer)); aside.append(disclaimer);
  layout.append(content, aside); $('page').append(layout); status('Карточка готова · есть ограничения данных');
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
async function ask(question) {
  if (!state.card) return;
  state.questionController?.abort(); const controller = new AbortController(); state.questionController = controller;
  const epoch = state.epoch, seq = ++state.questionSeq, ctx = {...state.ctx};
  const payload = {...ctx, insight_id: state.card.insight_id, question, request_id: crypto.randomUUID()};
  const target = $('reply'); target.replaceChildren(node('p', 'Готовим уточнение…')); target.setAttribute('aria-busy', 'true');
  $('submit').disabled = true; $('error').hidden = true; status('Готовим уточнение в текущем контексте…');
  try {
    const result = await request('/api/clarifications', ctx, controller, payload);
    if (epoch !== state.epoch || seq !== state.questionSeq) return;
    if (result.request_id !== payload.request_id || result.insight_id !== payload.insight_id) throw new Error('Ответ не соответствует запросу или версии карточки.');
    target.replaceChildren(); target.className = 'reply';
    target.append(node('strong', result.status === 'no_answer' ? 'Достоверный ответ недоступен' : 'Уточнение · есть ограничения'), node('p', question, 'muted'), node('p', result.text), sourceLinks(result.sources));
    status(result.status === 'no_answer' ? 'Недостаточно оснований для ответа' : 'Уточнение готово');
  } catch (e) {
    if (epoch !== state.epoch || seq !== state.questionSeq || e.name === 'AbortError') return;
    target.replaceChildren(); showError(e.message, () => ask(question)); status('Не удалось получить уточнение');
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
    $('profile-link').textContent = state.profile.name + ' · ' + state.data.companies.find(x => x.id === state.profile.company_id).name;
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
    const ctx = route.page === 'feed' ? {company_id: state.ctx.company_id, role: state.ctx.role} : {...state.ctx};
    const result = await request(route.page === 'feed' ? '/api/feed' : '/api/insight', ctx, controller);
    if (epoch !== state.epoch) return;
    $('page').replaceChildren(); if (route.page === 'feed') renderFeed(result); else renderCard(result);
  } catch (e) {
    if (epoch !== state.epoch || e.name === 'AbortError') return;
    $('page').replaceChildren(); showError(e.message, renderRoute); status('Не удалось открыть страницу');
    const a = node('a', 'Вернуться на главную'); a.href = '#/home'; $('page').append(a);
  } finally { if (epoch === state.epoch) $('page').setAttribute('aria-busy', 'false'); }
}
async function init() {
  try {
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 15000);
    let result;
    try { const response = await fetch('/api/bootstrap', {signal: controller.signal}); if (!response.ok) throw new Error('Сервис недоступен.'); result = await response.json(); }
    finally { clearTimeout(timeout); }
    state.data = result;
    const stored = readStored(PROFILE_KEY, result.profile);
    const valid = stored && typeof stored.name === 'string' && stored.name.trim().length > 0 && stored.name.length <= 100 && result.companies.some(c => c.id === stored.company_id) && Object.hasOwn(result.roles, stored.role);
    state.profile = valid ? {name: stored.name, company_id: stored.company_id, role: stored.role} : {...result.profile};
    const preferences = readStored(PREFERENCES_KEY, {}); state.preferences = preferences && typeof preferences === 'object' && !Array.isArray(preferences) ? preferences : {};
    $('notice').textContent = result.notice;
    await renderRoute();
  } catch (e) { showError('Не удалось подключить сервис. ' + e.message, init); status('Ошибка подключения'); $('page').setAttribute('aria-busy', 'false'); }
}
window.addEventListener('hashchange', () => { if (state.data) renderRoute(); });
document.querySelector('.skip').addEventListener('click', event => { event.preventDefault(); $('main').focus(); });
init();
