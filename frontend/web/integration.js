/* FE-08–FE-12: transport and UI boundary. Java DTO bindings await BE-12/13. */
'use strict';
(function (root) {
  const outcomes = {REFUSED: 'Недостаточно проверенных оснований для анализа', LIMITED: 'Анализ ограничен доступными данными', INSUFFICIENT_DATA: 'Недостаточно данных для обоснованного вывода', NO_RESULT: 'Подготовленного результата нет', FAILED: 'Не удалось подготовить анализ'};
  function safeURL(value, base = 'http://localhost') {
    if (typeof value !== 'string' || !value.trim() || /[\\\x00-\x20]/.test(value)) return null;
    try { const u = new URL(value, base); if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password) return null; return u.href; } catch { return null; }
  }
  function sourceURL(source, demo, base) {
    if (source.url !== undefined) {
      const href = safeURL(source.url, base); if (!href) return null;
      const u = new URL(href);
      if (Number.isInteger(source.page) && source.page > 0) u.hash = 'page=' + source.page;
      return u.href;
    }
    return demo && /^[a-z0-9-]+$/.test(source.id) ? '/sources/' + encodeURIComponent(source.id) : null;
  }
  function abortError() { const e = new Error('Ожидание остановлено'); e.name = 'AbortError'; return e; }
  function pause(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(abortError());
      const stop = () => { clearTimeout(timer); reject(abortError()); };
      const timer = setTimeout(() => { signal?.removeEventListener('abort', stop); resolve(); }, ms);
      signal?.addEventListener('abort', stop, {once: true});
    });
  }
  function createTransport({fetch: send, origin, maxWaitMs = 120000, sleep = pause, now = Date.now}) {
    const base = new URL(origin);
    const urlFor = value => { const href = safeURL(value, base.href); if (!href || new URL(href).origin !== base.origin || !new URL(href).pathname.startsWith('/api/v1/')) throw new Error('Недопустимый адрес API'); return href; };
    return async function transport(path, {method = 'GET', body, signal, requestId, onProgress = () => {}} = {}) {
      let url = urlFor(path), polling = false; const started = now();
      while (true) {
        if (signal?.aborted) throw abortError();
        const controller = new AbortController();
        const abort = () => controller.abort(); signal?.addEventListener('abort', abort, {once:true});
        const timer = setTimeout(() => controller.abort('timeout'), 15000);
        let response, data;
        try {
          response = await send(url, {method: polling ? 'GET' : method, credentials:'same-origin', signal:controller.signal,
            headers:{Accept:'application/json', ...(body && !polling ? {'Content-Type':'application/json'} : {}), ...(requestId && !polling ? {'Idempotency-Key':requestId} : {})},
            ...(body && !polling ? {body:JSON.stringify(body)} : {})});
          if (response.status === 401 || response.status === 403) { const e = new Error(response.status === 401 ? 'Сессия завершена. Войдите в интернет-банк заново.' : 'Нет доступа к данным выбранной компании.'); e.status=response.status; throw e; }
          data = response.status === 204 ? null : await response.json();
          if (!response.ok) { const e=new Error(data?.error?.message || data?.message || 'Сервис временно недоступен. Изменение не подтверждено.'); e.status=response.status; throw e; }
        } catch (e) { if (controller.signal.reason === 'timeout') throw new Error('Сервис не ответил за 15 секунд. Результат операции неизвестен.'); throw e; }
        finally { clearTimeout(timer); signal?.removeEventListener('abort',abort); }
        const pending = response.status === 202 || ['QUEUED','PENDING','RUNNING'].includes(data?.status);
        if (!pending) return data;
        const location = response.headers.get('Location');
        if (location) url = urlFor(location);
        else if (!polling) throw new Error('Сервис не передал адрес проверки операции.');
        polling = true;
        onProgress({status:data?.status || 'PENDING', location:url});
        if (now() - started >= maxWaitMs) { const e=new Error('Анализ ещё выполняется. Можно продолжить проверку без нового запуска.'); e.operationURL=url; e.resume=signal=>transport(url,{signal,onProgress}); throw e; }
        const header = response.headers.get('Retry-After');
        const seconds = header && /^\d+$/.test(header) ? Number(header) : (Date.parse(header)-now())/1000;
        await sleep(Math.min(30000, Math.max(500, Number.isFinite(seconds) ? seconds*1000 : 1500)), signal);
      }
    };
  }
  async function mapOperation(operation, mapper, signal) {
    try { return await mapper(await operation(),signal); }
    catch (e) { if (e.resume) { const resume=e.resume; e.resume=nextSignal=>mapOperation(()=>resume(nextSignal),mapper,nextSignal); } throw e; }
  }
  function createClient(config = {}) {
    if (!config.mode || config.mode === 'demo') return {mode:'demo'};
    if (config.mode !== 'server' || !config.bindings) throw new Error('Подключение серверного API ещё не настроено.');
    // Bindings translate agreed camelCase DTOs to the existing view model.
    const required = ['bootstrap','feed','insight','clarify','preferences','savePreference','opened'];
    for (const key of required) if (typeof config.bindings[key] !== 'function') throw new Error('Не настроен адаптер: ' + key);
    const transport = createTransport({fetch:(...args)=>fetch(...args), origin:location.origin});
    return {mode:'server', call:(name, input, options={}) => {
      if (typeof config.bindings[name] !== 'function') throw new Error('Каталог и анализ материалов пока недоступны в этом подключении.');
      return config.bindings[name](input, {...options, transport});
    }};
  }
  root.InsightsIntegration = {outcomes, sourceURL, safeURL, createTransport, createClient, mapOperation};
})(typeof globalThis === 'object' ? globalThis : this);
