'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {setup,walk}=require('./test_frontend.cjs');
const path=require('node:path');
const project=path.resolve(__dirname,'..');
const transportSource=fs.readFileSync(path.join(project,'web/integration.js'),'utf8');
const source=transportSource+'\n'+fs.readFileSync(path.join(project,'web/app.js'),'utf8');
const sandbox={URL,setTimeout,clearTimeout,AbortController}; vm.createContext(sandbox);vm.runInContext(transportSource,sandbox);
const {sourceURL,createTransport}=sandbox.InsightsIntegration;
const response=(status,data,headers={})=>({status,ok:status<400,headers:{get:k=>headers[k]||null},json:async()=>data});
async function main(){
  assert.equal(sourceURL({id:'opaque:/123',url:'https://example.org/report.pdf',page:7},false),'https://example.org/report.pdf#page=7');
  for(const url of ['javascript:alert(1)','data:text/html,x','https://user:pass@example.org','//evil.org/a\\b']) assert.equal(sourceURL({url},false),null);
  assert.equal(sourceURL({id:'bank-1'},false),null);
  assert.equal(sourceURL({id:'bank-1'},true),'/sources/bank-1');
  let calls=[],waits=[];
  const queue=[response(202,{status:'QUEUED'},{Location:'/api/v1/operations/1','Retry-After':'2'}),response(200,{status:'RUNNING'},{'Retry-After':'1'}),response(200,{status:'COMPLETED',value:42})];
  const transport=createTransport({origin:'https://bank.test',fetch:async(url,options)=>{calls.push({url,options});return queue.shift();},sleep:async ms=>waits.push(ms)});
  assert.equal((await transport('/api/v1/insights',{method:'POST',body:{eventId:'one'},requestId:'stable-key'})).value,42);
  assert.deepEqual(calls.map(c=>c.options.method),['POST','GET','GET']);assert.equal(calls[0].options.headers['Idempotency-Key'],'stable-key');assert.deepEqual(waits,[2000,1000]);
  await assert.rejects(createTransport({origin:'https://bank.test',fetch:async()=>response(202,{}, {Location:'https://evil.org/api/v1/1'})})('/api/v1/insights'),/адрес/);
  for(const code of [401,403]) await assert.rejects(createTransport({origin:'https://bank.test',fetch:async()=>response(code,{})})('/api/v1/session'),e=>e.status===code);
  let polls=0;
  const delayed=createTransport({origin:'https://bank.test',maxWaitMs:0,fetch:async()=>++polls===1 ? response(202,{status:'RUNNING'},{Location:'/api/v1/operations/resume'}) : response(200,{value:7})});
  let waiting;try{await sandbox.InsightsIntegration.mapOperation(()=>delayed('/api/v1/insights'),x=>({normalized:x.value}));}catch(e){waiting=e;}
  assert(waiting.resume);assert.equal((await waiting.resume()).normalized,7);
  const controller=new AbortController();controller.abort();await assert.rejects(transport('/api/v1/feed',{signal:controller.signal}),e=>e.name==='AbortError');
  const profile={name:'Серверный пользователь',company_id:'c',role:'ceo'};
  const context={company_id:'c',role:'ceo',event_id:'e',view:'overview'};
  const card={context,insight_id:'v1',title:'Событие',blocks:[],sources:[],missing_data:[],disclaimer:'Не является инвестрекомендацией',outcome:'LIMITED'};
  let saved=[],opened=0, failSave=false, deny=false;
  const bindings={
    bootstrap:async()=>({profile,companies:[{id:'c',name:'Компания',roles:['ceo']}],roles:{ceo:'Директор'},views:{overview:'Обзор'},notice:'Тест адаптера'}),
    preferences:async()=>({}),feed:async()=>({context:{company_id:'c',role:'ceo'},items:[],empty_reason:'Нет подходящих событий'}),
    insight:async()=>{if(deny){let e=new Error('Нет доступа');e.status=403;throw e;}return card;},clarify:async()=>{},
    savePreference:async input=>{if(failSave)throw new Error('offline');saved.push(input);},opened:async()=>{opened++;}
  };
  const ui=setup(source,'',new Map(),'#/insight/e');ui.run('location.origin="https://bank.test"');
  ui.run('window.INSIGHTS_CONFIG={mode:"server",bindings:{}}');
  const config=ui.run('window.INSIGHTS_CONFIG');config.bindings=bindings;
  await ui.run('init()');assert(ui.text().includes('Анализ ограничен'));assert.equal(opened,1);
  let useful=walk(ui.root).find(e=>e.tagName==='button'&&e.textContent==='Полезно');
  failSave=true;await useful.onclick();assert.equal(useful.getAttribute('aria-pressed'),'false');assert.equal(ui.storage.size,0);
  failSave=false;await useful.onclick();assert.equal(useful.getAttribute('aria-pressed'),'true');assert.equal(saved[0].insight_id,'v1');
  ui.location.hash='#/profile';await ui.run('renderRoute()');assert(!ui.doc.getElementById('login-form'));assert.equal(ui.doc.getElementById('profile-name').readOnly,true);
  ui.location.hash='#/insight/e';deny=true;await ui.run('renderRoute()');assert.equal(ui.run('state.card'),null);assert.equal(ui.run('state.data'),null);assert(ui.doc.getElementById('error').textContent.includes('Нет доступа'));
  console.log('PASS: safe source URLs, PDF pages, opaque IDs, async polling, Retry-After, idempotency, abort, 401/403, server session, feedback failure/success, access reset.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
