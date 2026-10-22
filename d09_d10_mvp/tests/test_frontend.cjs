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
function setup(source, base, storage = new Map(), hash = '#/home') {
  const root = new Element('body');
  for (const id of ['main','page','error','status','notice','profile-link']) { const e = new Element('div'); e.id = id; root.append(e); }
  for (const name of ['brand','skip']) { const e = new Element('a'); e.className = name; root.append(e); }
  for (const name of ['home','profile','feed']) { const e = new Element('a'); e.dataset.nav = name; root.append(e); }
  const doc = {createElement: tag => new Element(tag), getElementById: id => walk(root).find(n => n.id === id),
    querySelector: selector => walk(root).find(n => n.className === selector.slice(1)),
    querySelectorAll: selector => selector === '[data-nav]' ? walk(root).filter(n => n.dataset.nav) : []};
  const location = {hash}; const requests = [];
  const sandbox = {document:doc, location, history:{replaceState: (_s,_t,url) => {location.hash=url;}},
    window:{addEventListener(){}}, localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)},
    URLSearchParams, AbortController, crypto:webcrypto, setTimeout, clearTimeout,
    fetch: (url, options) => {requests.push({url,options}); return fetch(base+url,options);}};
  vm.createContext(sandbox); vm.runInContext(source.slice(0, source.lastIndexOf('init();')), sandbox);
  return {root,doc,storage,requests,location,run:code=>vm.runInContext(code,sandbox), text:()=>doc.getElementById('page').textContent};
}
async function main() {
  const child = spawn('python',['server.py','--port','0','--delay','0'],{cwd:project,windowsHide:true,stdio:['ignore','pipe','pipe']});
  const base = await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Server startup timeout')),10000);
    child.once('error',reject); child.stdout.once('data',data=>{clearTimeout(timer);resolve(String(data).trim().split(' ')[1]);});
  });
  try {
    const source = await readFile(path.join(project,'web/app.js'),'utf8');
    const app = setup(source,base); await app.run('init()');
    assert(!app.text().includes('Ваш контекст'));
    assert(app.doc.querySelectorAll('[data-nav]').map(n=>n.dataset.nav).join(',') === 'home,profile,feed');

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
    assert.deepEqual(headingText('h2').slice(0,4),['Факт события','Аналитика по открытым источникам','Аналитика от Альфа-банка','Вывод для компании']);
    assert.deepEqual(headingText('h3'),Array(3).fill(['Последствия','Риски','Рекомендации']).flat());
    assert(reloaded.text().includes('Не является инвестрекомендацией'));
    for (const [view,label] of [['consequences','Последствия'],['risks','Риски'],['recommendations','Рекомендации']]) {
      reloaded.location.hash='#/insight/rate?view='+view; await reloaded.run('renderRoute()');
      assert.deepEqual(headingText('h3'),[label,label,label]);
      assert(headingText('h2').includes('Уточнить по этому событию'));
      await reloaded.run("ask('Какие рекомендации?')");
      assert(reloaded.doc.getElementById('reply').textContent.includes('Проверьте устойчивость инвестиционных планов'));
    }
    const useful = walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Полезно'); useful.onclick();
    assert(JSON.parse(app.storage.get('d09-d10-preferences-v1'))['energy:ceo:rate'].feedback==='useful');
    reloaded.location.hash='#/feed'; await reloaded.run('renderRoute()');
    const hide = walk(reloaded.root).find(e=>e.getAttribute('aria-label')==='Скрыть событие «Пауза в снижении ставки»'); hide.onclick();
    await reloaded.run('renderRoute()');
    assert(!walk(reloaded.root).some(e=>e.tagName==='h2' && e.textContent==='Пауза в снижении ставки'));
    assert(reloaded.text().includes('Скрыто событий: 1'));
    const restore=walk(reloaded.root).find(e=>e.tagName==='button' && e.textContent==='Показать скрытые'); restore.onclick();
    await reloaded.run('renderRoute()');
    assert(walk(reloaded.root).some(e=>e.tagName==='h2' && e.textContent==='Пауза в снижении ставки'));
    console.log('PASS: profile persistence, URL isolation, login non-submission, overview, three views, clarifications, feedback, hide/restore.');
  } finally {child.kill();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
