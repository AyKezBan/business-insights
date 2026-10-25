// Behaviour tests using a small DOM adapter; this is not browser/layout testing.
const {readFile} = require('node:fs/promises');
const {spawn} = require('node:child_process');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const {webcrypto} = require('node:crypto');
const project = path.resolve(__dirname, '..');

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.dataset = {}; this.value = ''; this.hidden = false; this.listeners = {}; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ''; this.children = nodes; }
  setAttribute(key,value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(key,fn) { this.listeners[key] = fn; }
  focus() {}
  setCustomValidity(message) { this.validityMessage = message; }
  reportValidity() { return !this.validityMessage; }
}
const walk = root => [root, ...root.children.flatMap(walk)];
function deferred() { let resolve, reject; const promise = new Promise((yes,no)=>{resolve=yes;reject=no;}); promise.catch(()=>{}); return {promise,resolve,reject}; }
const jsonResponse = value => ({ok:true,json:async()=>value});
const waitFor = async predicate => { const until=Date.now()+2000; while(Date.now()<until){if(predicate()) return; await new Promise(resolve=>setTimeout(resolve,1));} throw new Error('Timed out waiting for deferred request'); };
function setup(source, base, storage = new Map(), hash = '#/home', fetchOverride = null) {
  const root = new Element('body');
  for (const id of ['main','page','error','status','notice','profile-link']) { const e = new Element('div'); e.id = id; root.append(e); }
  for (const name of ['brand','skip']) { const e = new Element('a'); e.className = name; root.append(e); }
  for (const name of ['home','profile','feed']) { const e = new Element('a'); e.dataset.nav = name; root.append(e); }
  const doc = {createElement: tag => new Element(tag), getElementById: id => walk(root).find(n => n.id === id),
    querySelector: selector => walk(root).find(n => n.className === selector.slice(1)),
    querySelectorAll: selector => selector === '[data-nav]' ? walk(root).filter(n => n.dataset.nav) : []};
  const location = {hash}; const requests = [];
  const sandbox = {document:doc, location, history:{replaceState: (_s,_t,url) => {location.hash=url;}},
    window:{addEventListener(){}}, localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    URL, URLSearchParams, AbortController, crypto:webcrypto, setTimeout, clearTimeout,
    fetch: (url, options) => {requests.push({url,options}); return fetchOverride ? fetchOverride(url,options,base) : fetch(base+url,options);}};
  vm.createContext(sandbox); vm.runInContext(source.slice(0, source.lastIndexOf('init();')), sandbox);
  return {root,doc,storage,requests,location,run:code=>vm.runInContext(code,sandbox), text:()=>doc.getElementById('page').textContent};
}
async function testStaleCardResponses(source, base, mode) {
  let releaseStarted; const started=new Promise(resolve=>{releaseStarted=resolve;}); const hold=deferred(); let oldPayload;
  let held=false;
  const fake=async (url,options,origin)=>{
    if(url.startsWith('/api/insight?') && url.includes('company_id=retail') && !held) {
      held=true; releaseStarted(); oldPayload=await (await fetch(origin+url,options)).json(); return hold.promise;
    }
    return fetch(origin+url,options);
  };
  const app=setup(source,base,new Map(),'#/insight/rate?view=overview',fake);
  const initial=app.run('init()'); await started;
  app.run("state.profile={...state.profile,company_id:'energy'}");
  app.location.hash='#/insight/rate?view=overview'; await app.run('renderRoute()');
  assert(app.text().includes('Для энергетики'));
  if(mode==='success') hold.resolve(jsonResponse(oldPayload)); else hold.reject(new Error('late old-context failure'));
  await initial;
  assert(app.text().includes('Для энергетики')); assert.equal(app.doc.getElementById('error').hidden,true);
}
async function testQuestionRaces(source, base) {
  const pending=[]; const counts=new Map();
  const fake=async (url,options,origin)=>{
    if(!url.startsWith('/api/clarifications')) return fetch(origin+url,options);
    const payload=JSON.parse(options.body); const count=(counts.get(payload.question)||0)+1; counts.set(payload.question,count);
    const d=deferred(); pending.push({payload,d,signal:options.signal,count});
    if(payload.question==='Сетевая ошибка' && count===1) return Promise.reject(new TypeError('offline'));
    if(payload.question==='Несовпавший контекст') return jsonResponse({context:{...payload,company_id:'energy'},request_id:payload.request_id,insight_id:payload.insight_id,status:'answered',text:'wrong context',sources:[],missing_data:[]});
    if(payload.question==='Сетевая ошибка' && count===2) return jsonResponse({context:{company_id:payload.company_id,role:payload.role,event_id:payload.event_id,view:payload.view},request_id:payload.request_id,insight_id:payload.insight_id,status:'answered',text:'Повтор успешен',sources:[],missing_data:[]});
    return d.promise;
  };
  const app=setup(source,base,new Map(),'#/insight/rate?view=overview',fake); await app.run('init()');
  const first=app.run("ask('Первый вопрос')"); await waitFor(()=>pending.length===1);
  const second=app.run("ask('Второй вопрос')"); await waitFor(()=>pending.length===2);
  assert.equal(pending[0].signal.aborted,true);
  const answer=(item,text)=>({context:{company_id:item.payload.company_id,role:item.payload.role,event_id:item.payload.event_id,view:item.payload.view},request_id:item.payload.request_id,insight_id:item.payload.insight_id,status:'answered',text,sources:[],missing_data:[]});
  pending[1].d.resolve(jsonResponse(answer(pending[1],'Ответ на второй'))); await second;
  assert(app.doc.getElementById('reply').textContent.includes('Ответ на второй'));
  pending[0].d.resolve(jsonResponse(answer(pending[0],'Запоздавший ответ'))); await first;
  assert(app.doc.getElementById('reply').textContent.includes('Ответ на второй'));
  assert.equal(app.doc.getElementById('submit').disabled,false);

  await app.run("ask('Сетевая ошибка')");
  assert(app.doc.getElementById('error').textContent.includes('offline'));
  assert.equal(app.doc.getElementById('submit').disabled,false);
  const retry=walk(app.root).find(e=>e.tagName==='button' && e.textContent==='Повторить'); assert(retry); retry.onclick();
  await waitFor(()=>app.doc.getElementById('reply').textContent.includes('Повтор успешен'));
  assert.equal(app.doc.getElementById('error').hidden,true); assert.equal(app.doc.getElementById('submit').disabled,false);

  await app.run("ask('Несовпавший контекст')");
  assert(app.doc.getElementById('error').textContent.includes('другого контекста'));
  assert.equal(app.doc.getElementById('submit').disabled,false);
}
async function main() {
  const child = spawn(process.env.PYTHON || 'python',['server.py','--port','0','--delay','0'],{cwd:project,windowsHide:true,stdio:['ignore','pipe','pipe']});
  const base = await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Server startup timeout')),10000);
    child.once('error',reject); child.stdout.once('data',data=>{clearTimeout(timer);resolve(String(data).trim().split(' ')[1]);});
  });
  try {
    const source = await readFile(path.join(project,'web/integration.js'),'utf8') + '\n' + await readFile(path.join(project,'web/app.js'),'utf8');
    const app = setup(source,base); await app.run('init()');
    assert(!app.text().includes('Ваш контекст'));
    assert(app.doc.querySelectorAll('[data-nav]').map(n=>n.dataset.nav).join(',') === 'home,profile,feed');

    const corrupt = new Map([['d09-d10-profile-v1','{broken'],['d09-d10-preferences-v1','[]']]);
    const recovery=setup(source,base,corrupt); await recovery.run('init()'); assert.equal(recovery.run('state.profile.company_id'),'retail');
    const invalidPrefs = new Map([['d09-d10-preferences-v1',JSON.stringify({
      'retail:ceo:rate':{hidden:true,feedback:'legacy'},
      'retail:ceo:rate:retail:rate:v2':{feedback:'not_useful',reason:'<script>'},
      'retail:ceo:rate:retail:rate:v3':{feedback:'maybe',reason:'basis'},
      'retail:ceo:rate:broken':'useful'
    })]]);
    const sanitized=setup(source,base,invalidPrefs); await sanitized.run('init()');
    assert.equal(sanitized.run("state.preferences['retail:ceo:rate'].hidden"),true);
    assert.equal(sanitized.run("state.preferences['retail:ceo:rate:retail:rate:v2'].reason"),undefined);
    assert.equal(sanitized.run("state.preferences['retail:ceo:rate:retail:rate:v3']"),undefined);
    assert.equal(sanitized.run("state.preferences['retail:ceo:rate:broken']"),undefined);
    const blocked = new Map(); blocked.set = () => { throw new Error('storage blocked'); };
    const blockedApp=setup(source,base,blocked,'#/insight/rate?view=overview'); await blockedApp.run('init()');
    walk(blockedApp.root).find(e=>e.tagName==='button' && e.textContent==='Полезно').onclick();
    assert(blockedApp.doc.getElementById('page').textContent.includes('Не удалось сохранить оценку'));

    app.location.hash='#/profile'; await app.run('renderRoute()');
    app.doc.getElementById('profile-name').value='Тестовый пользователь';
    app.doc.getElementById('profile-company').value='energy'; app.doc.getElementById('profile-role').value='ceo';
    await app.doc.getElementById('profile-form').onsubmit({preventDefault(){}});
    const saved = JSON.parse(app.storage.get('d09-d10-profile-v1'));
    assert.equal(saved.company_id,'energy'); assert.equal(saved.role,'ceo');
    const sentBefore = app.requests.length;
    app.doc.getElementById('login').value='test-login'; app.doc.getElementById('password').value='secret-not-to-store';
    app.doc.getElementById('login-form').onsubmit({preventDefault(){}});
    assert.equal(app.requests.length,sentBefore); assert.equal(app.doc.getElementById('password').value,'');
    assert(!JSON.stringify([...app.storage]).includes('secret-not-to-store'));
    assert(app.doc.getElementById('login-status').textContent.includes('не отправлены'));

    const reloaded = setup(source,base,app.storage,'#/insight/rate?company_id=retail&role=cfo&view=overview');
    await reloaded.run('init()');
    assert.equal(reloaded.run('state.ctx.company_id'),'energy'); assert.equal(reloaded.run('state.ctx.role'),'ceo');
    assert(!reloaded.location.hash.includes('company_id')); assert(reloaded.text().includes('Для энергетики'));
    const headingText = kind => walk(reloaded.root).filter(e=>e.tagName===kind).map(e=>e.textContent);
    assert.deepEqual(headingText('h2').slice(0,4),['Что произошло','Влияние на компанию','Риски и неопределённость','Что проверить дальше']);
    assert(!reloaded.text().includes('Аналитика по открытым источникам'));
    assert.equal(walk(reloaded.root).filter(e=>e.tagName==='details').length,3);
    assert.equal(walk(reloaded.root).filter(e=>e.tagName==='summary' && e.textContent==='На чём основан вывод').length,3);
    assert.equal(walk(reloaded.root).filter(e=>String(e.className||'').startsWith('source-origin ')).length,6);
    for(const text of ['Факт события','Вывод AI по внешним источникам','Оценка банка','не официальная позиция банка','Синтетические материалы','Не является инвестрекомендацией']) assert(reloaded.text().includes(text));
    assert(reloaded.text().includes('Сохранение ставки само по себе не меняет условия договоров'));
    assert(reloaded.text().includes('Быстрое удешевление нового финансирования не гарантировано'));
    assert(reloaded.text().includes('Для энергетики: стоимость нового финансирования'));
    for(const a of walk(reloaded.root).filter(e=>e.tagName==='a' && e.className==='source-link')) {assert(a.href.startsWith('/sources/')); assert.equal(a.rel,'noopener noreferrer'); assert.equal(a.target,'_blank');}
    assert(reloaded.text().includes('Не является инвестрекомендацией'));
    for (const [view,label] of [['consequences','Влияние на компанию'],['risks','Риски и неопределённость'],['recommendations','Что проверить дальше']]) {
      reloaded.location.hash='#/insight/rate?view='+view; await reloaded.run('renderRoute()');
      assert(reloaded.requests.at(-1).url.includes('view=overview'));
      assert.deepEqual(headingText('h2').filter(t=>['Влияние на компанию','Риски и неопределённость','Что проверить дальше'].includes(t)),[label]);
      assert.equal(walk(reloaded.root).filter(e=>e.tagName==='details').length,1);
      assert(reloaded.text().includes('Вывод AI для компании · не официальная позиция банка'));
      assert(headingText('h2').includes('Уточнить по этому событию'));
      await reloaded.run("ask('Какие рекомендации?')");
      assert.equal(JSON.parse(reloaded.requests.at(-1).options.body).view,view);
      assert(reloaded.doc.getElementById('reply').textContent.includes('Проверьте устойчивость инвестиционных планов'));
    }
    const negative = walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Не полезно'); negative.onclick();
    assert(!negative.hidden);
    const reasonSelect=walk(reloaded.root).find(e=>e.tagName==='select' && e.getAttribute('aria-label')==='Причина оценки');
    reasonSelect.value='basis'; walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Сохранить оценку').onclick();
    const prefs=JSON.parse(reloaded.storage.get('d09-d10-preferences-v1'));
    assert.equal(prefs['energy:ceo:rate:energy:rate:v2'].feedback,'not_useful'); assert.equal(prefs['energy:ceo:rate:energy:rate:v2'].reason,'basis');
    const useful = walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Полезно'); useful.onclick();
    assert.equal(JSON.parse(reloaded.storage.get('d09-d10-preferences-v1'))['energy:ceo:rate:energy:rate:v2'].feedback,'useful');
    assert.equal(JSON.parse(reloaded.storage.get('d09-d10-preferences-v1'))['energy:ceo:rate:energy:rate:v2'].reason,null);
    assert.equal(JSON.parse(reloaded.storage.get('d09-d10-preferences-v1'))['energy:ceo:rate:energy:rate:v2'].previousNegativeReason,'basis');
    walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Не полезно').onclick();
    const reopenedReason=walk(reloaded.root).find(e=>e.tagName==='select' && e.getAttribute('aria-label')==='Причина оценки');
    assert.equal(reopenedReason.value,'basis');
    walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Сохранить оценку').onclick();
    assert.equal(JSON.parse(reloaded.storage.get('d09-d10-preferences-v1'))['energy:ceo:rate:energy:rate:v2'].reason,'basis');
    const restoredFeedback=setup(source,base,reloaded.storage,'#/insight/rate?view=overview'); await restoredFeedback.run('init()');
    walk(restoredFeedback.root).find(e=>e.tagName==='button' && e.textContent==='Не полезно').onclick();
    assert.equal(walk(restoredFeedback.root).find(e=>e.tagName==='select' && e.getAttribute('aria-label')==='Причина оценки').value,'basis');
    const versionPanel=reloaded.run("state.card.insight_id='energy:rate:v3'; feedbackPanel(state.card)");
    assert.equal(versionPanel.children.find(e=>e.tagName==='div').children[0].getAttribute('aria-pressed'),'false');
    const staleCard=reloaded.run("state.card.insight_id='energy:rate:v2'; feedbackPanel(state.card)");
    reloaded.run("state.card.insight_id='retail:rate:v2'");
    staleCard.children.find(e=>e.tagName==='div').children[0].onclick();
    assert.equal(JSON.parse(reloaded.storage.get('d09-d10-preferences-v1'))['energy:ceo:rate:energy:rate:v2'].feedback,'not_useful');
    reloaded.location.hash='#/insight/route?view=overview'; await reloaded.run('renderRoute()');
    assert(reloaded.text().includes('Релевантного материала банка по этой теме в доступном наборе нет.'));
    assert(reloaded.text().includes('Для этого события оценка банка недоступна'));
    assert.equal((reloaded.text().match(/Для этого события оценка банка недоступна/g)||[]).length,1);
    const noBank = reloaded.run(`(() => { const card={...state.card,blocks:state.card.blocks.filter(b=>b.kind!=='bank')}; $('page').replaceChildren(); renderCard(card); return $('page').textContent; })()`);
    assert(noBank.includes('Для этого события оценка банка недоступна'));
    const partialBank = reloaded.run(`(() => { state.ctx.view='overview'; const bank={kind:'bank',sections:[{kind:'recommendations',certainty:'assessment',text:'Учебная оценка доступна по этой теме.',source_ids:[]},{kind:'risks',certainty:'unavailable',text:'Нет данных',source_ids:[]},{kind:'consequences',certainty:'unavailable',text:'Нет данных',source_ids:[]}]}; const card={...state.card,blocks:[...state.card.blocks.filter(b=>b.kind!=='bank'),bank]}; $('page').replaceChildren(); renderCard(card); return $('page').textContent; })()`);
    assert(partialBank.includes('Учебная оценка доступна по этой теме.'));
    assert(!partialBank.includes('Для этого события оценка банка недоступна'));
    const omittedSections = reloaded.run(`(() => { const card={...state.card,blocks:state.card.blocks.map(b=>b.kind==='bank'?{...b,sections:b.sections.filter(s=>s.kind!=='risks')}:b.kind==='open'?{...b,sections:b.sections.filter(s=>s.kind!=='risks')}:b.kind==='ai'?{...b,sections:b.sections.filter(s=>s.kind!=='risks')}:b)}; $('page').replaceChildren(); renderCard(card); return $('page').textContent; })()`);
    assert(omittedSections.includes('Вывод AI для компании · не официальная позиция банкаВывод для компании по этой теме недоступен: раздел не предоставлен.Нет данных'));
    assert(omittedSections.includes('Вывод AI по внешним источникамРаздел по этой теме не предоставлен.Нет данных'));
    assert(omittedSections.includes('Оценка банка · в этой демонстрации учебнаяРаздел по этой теме не предоставлен.Нет данных'));
    const unknownCertainty = reloaded.run(`(() => { const card={...state.card,blocks:state.card.blocks.map(b=>b.kind==='ai'?{...b,sections:b.sections.map(s=>({...s,certainty:'future-type'}))}:b)}; $('page').replaceChildren(); renderCard(card); return $('page').textContent; })()`);
    assert(unknownCertainty.includes('Тип утверждения не указан'));
    reloaded.location.hash='#/feed'; await reloaded.run('renderRoute()');
    const hide = walk(reloaded.root).find(e=>e.getAttribute('aria-label')==='Скрыть событие «Пауза в снижении ставки»'); hide.onclick();
    await reloaded.run('renderRoute()');
    assert(!walk(reloaded.root).some(e=>e.tagName==='h2' && e.textContent==='Пауза в снижении ставки'));
    assert(reloaded.text().includes('Скрыто событий: 1'));
    const restore=walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Показать скрытые'); restore.onclick();
    await reloaded.run('renderRoute()');
    assert(walk(reloaded.root).some(e=>e.tagName==='h2' && e.textContent==='Пауза в снижении ставки'));
    await testStaleCardResponses(source,base,'success'); await testStaleCardResponses(source,base,'error');
    await testQuestionRaces(source,base);
    console.log('PASS: profile/context, corrupt and blocked storage, 3 thematic disclosures, content/provenance, missing bank material, source safety, filters, clarification races/mismatch/network retry, versioned feedback, hide/restore.');
  } finally {child.kill();}
}
module.exports = {setup, walk};
if (require.main === module) main().catch(error=>{console.error(error);process.exitCode=1;});
