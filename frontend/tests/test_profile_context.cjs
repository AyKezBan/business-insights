// Offline DOM behavior tests: two fresh JS VMs share only browser storage.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {setup,walk} = require('./test_frontend.cjs');
const web = path.resolve(__dirname,'../web');
const appSource = ['integration.js','backend-bindings.js','app.js'].map(file=>fs.readFileSync(path.join(web,file),'utf8')).join('\n');
const publicId = '11111111-1111-4111-8111-111111111111';
const answerId = '22222222-2222-4222-8222-222222222222';
const response = (status,data) => ({status,ok:status<400,json:async()=>data,headers:{get:()=>null}});
const context = ui => JSON.parse(ui.run('JSON.stringify({company_id:state.profile?.company_id,role:state.profile?.role})'));
function page(storage,options={}) {
  const selected=options.selected || {company_id:'U_2',role:'ceo'};
  const dto={insightId:publicId,companyId:selected.company_id,role:selected.role,eventId:'synthetic:event',status:'SUCCEEDED',mlStatus:'complete',outcome:'full',
    headline:'Сохранённый синтетический инсайт',sources:[],missingData:[],warnings:[],feedback:{rating:'USEFUL',comment:null},
    whatHappened:[],whyItMatters:[],consequences:[],risks:[],opportunities:[],actions:[],bankView:[],scenarios:[],modelVsBank:[]};
  const ui=setup(appSource,'',storage,options.hash || '#/profile',async(url,request)=>{
    const parsed=new URL(url,'https://test.local');
    if (parsed.pathname==='/api/v1/session') return options.sessionResponse ? options.sessionResponse() : response(options.sessionDenied || 200,{personId:options.person || 'person-A'});
    if (parsed.pathname==='/api/v1/companies') return options.companiesResponse ? options.companiesResponse() : response(200,options.companies || [{companyId:'U_1'},{companyId:'U_2'}]);
    if (options.denied) return response(options.denied,{});
    if (/\/companies\/U_[12]\/insights$/.test(parsed.pathname)) return response(200,{items:parsed.pathname.includes('/'+selected.company_id+'/') && parsed.searchParams.get('role')===selected.role ? [dto] : []});
    if (parsed.pathname==='/api/v1/insights/'+publicId) return response(200,dto);
    if (parsed.pathname.endsWith('/opened')) return response(204,null);
    if (parsed.pathname.endsWith('/questions')) return response(200,{operationId:answerId,kind:'QUESTION',status:'SUCCEEDED',parentInsightId:publicId});
    if (parsed.pathname==='/api/v1/answers/'+answerId) return response(200,{operationId:answerId,insightId:publicId,status:'SUCCEEDED',outcome:'full',mlStatus:'answered',companyId:selected.company_id,eventId:dto.eventId,text:'Ответ в сохранённом контексте',sources:[],missingData:[],warnings:[],claims:[]});
    throw new Error('Unexpected endpoint '+url+' '+request.method);
  });
  ui.run('location.origin="https://test.local";window.INSIGHTS_CONFIG={mode:"server",bindings:InsightsBackend.createBindings()}');
  return ui;
}
async function select(ui,company='U_2',role='ceo') {
  ui.location.hash='#/profile'; await ui.run('renderRoute()');
  ui.doc.getElementById('profile-company').value=company;
  ui.doc.getElementById('profile-company').onchange();
  ui.doc.getElementById('profile-role').value=role;
  ui.doc.getElementById('profile-name').value='Ignored forged display name';
  await ui.doc.getElementById('profile-form').onsubmit({preventDefault(){}});
}
const notices = ui => ui.doc.getElementById('notice').textContent+' '+ui.doc.getElementById('status').textContent;
async function savedContext() {
  const storage=new Map(); const ui=page(storage); await ui.run('init()'); await select(ui);
  return {storage,key:[...storage.keys()][0]};
}
async function main() {
  for (const selected of [{company_id:'U_2',role:'ceo'},{company_id:'U_1',role:'ceo'},{company_id:'U_2',role:'cfo'},{company_id:'U_1',role:'cfo'}]) {
    const storage=new Map(), first=page(storage,{selected}); await first.run('init()'); await select(first,selected.company_id,selected.role);
    first.location.hash='#/insight/'+publicId; await first.run('renderRoute()');
    assert(first.text().includes('Сохранённый синтетический инсайт'));
    const reload=page(storage,{selected,hash:first.location.hash}); await reload.run('init()');
    assert.deepEqual(context(reload),selected,'Full reload must preserve explicit company/role for this authenticated person');
    assert.equal(reload.run('state.data.person_id'),'person-A');
    assert.equal(reload.run('state.profile.name'),'person-A','Stored preferences cannot replace server identity/name');
    assert.equal(walk(reload.root).find(e=>e.tagName==='button' && e.textContent==='Полезно')?.getAttribute('aria-pressed'),'true','Server feedback remains usable after full reload');
    assert(reload.doc.getElementById('question-form')); await reload.run('ask("Синтетический вопрос")');
    assert(reload.doc.getElementById('reply').textContent.includes('Ответ в сохранённом контексте'));
    assert([...first.requests,...reload.requests].every(r=>!r.url.includes('/analyses')),'Profile/reload never posts an analysis');
    assert.equal(storage.size,1); assert.deepEqual(JSON.parse([...storage.values()][0]),selected,'Only selected company and role are persisted');
    assert(![...storage.values()].join('').includes('Синтетический вопрос'));
  }
  const {storage,key}=await savedContext();
  const personB=page(storage,{person:'person-B'}); await personB.run('init()');
  assert.deepEqual(context(personB),{company_id:'U_1',role:'cfo'},'Another person must not inherit the selection');
  await select(personB,'U_1','ceo'); assert.equal(storage.size,2);
  const personA=page(storage); await personA.run('init()'); assert.deepEqual(context(personA),{company_id:'U_2',role:'ceo'});
  assert.equal(personA.run('state.profile.name'),'person-A');
  assert(!personA.text().includes('доступные роли получены из серверной сессии'));

  for (const value of ['{broken',JSON.stringify([]),JSON.stringify({company_id:'removed',role:'ceo'}),JSON.stringify({company_id:'U_2',role:'admin'}),JSON.stringify({company_id:'U_2',role:'constructor'})]) {
    const invalid=new Map([[key,value]]), ui=page(invalid); await ui.run('init()');
    assert.deepEqual(context(ui),{company_id:'U_1',role:'cfo'}); assert.match(notices(ui),/сохранённый выбор|сохраненный выбор/i);
  }
  const removed=page(new Map([[key,JSON.stringify({company_id:'U_2',role:'ceo'})]]),{companies:[{companyId:'U_1'}]});
  await removed.run('init()'); assert.deepEqual(context(removed),{company_id:'U_1',role:'cfo'});
  const restricted=page(new Map([[key,JSON.stringify({company_id:'U_2',role:'ceo'})]]),{companies:[{companyId:'U_1',roles:['cfo']},{companyId:'U_2',roles:['cfo']}]});
  await restricted.run('init()'); assert.deepEqual(context(restricted),{company_id:'U_1',role:'cfo'});
  await select(restricted,'U_2','ceo'); assert.deepEqual(context(restricted),{company_id:'U_1',role:'cfo'},'Form tampering cannot bypass explicit company role allowlist');
  const ceoOnly=page(new Map(),{companies:[{companyId:'U_1',roles:['ceo']}]}); await ceoOnly.run('init()');
  assert.deepEqual(context(ceoOnly),{company_id:'U_1',role:'ceo'},'Fresh default must also honor a future explicit allowlist');
  const noRoles=page(new Map(),{companies:[{companyId:'U_1',roles:[]}]}); await noRoles.run('init()'); assert.equal(noRoles.run('state.data'),null);

  const deniedStorage=new Map(); deniedStorage.set=()=>{throw new Error('Blocked storage');};
  const temporary=page(deniedStorage); await temporary.run('init()'); await select(temporary);
  assert.deepEqual(context(temporary),{company_id:'U_2',role:'ceo'},'Storage restrictions must not prevent an explicit current-session selection');
  assert.match(notices(temporary),/текущ|перезагруз/); assert.match(notices(temporary),/не удалось|не разреш/i);
  const afterTemporary=page(deniedStorage); await afterTemporary.run('init()'); assert.deepEqual(context(afterTemporary),{company_id:'U_1',role:'cfo'});
  const unreadable=new Map(); unreadable.get=()=>{throw new Error('Blocked storage');};
  const fallback=page(unreadable); await fallback.run('init()'); assert.deepEqual(context(fallback),{company_id:'U_1',role:'cfo'}); assert.match(notices(fallback),/не удалось.*выбор/i);

  for (const denied of [401,403]) {
    const authStorage=new Map(storage), options={}; const ui=page(authStorage,options); await ui.run('init()');
    options.denied=denied; ui.location.hash='#/feed'; await ui.run('renderRoute()');
    assert.equal(ui.run('state.data'),null); assert.equal(ui.run('state.profile'),null); assert.equal(ui.run('state.card'),null);
    assert(!authStorage.has(key),'401/403 removes active person selection'); assert.equal(authStorage.size,1,'Other person preference remains isolated');
    assert(!ui.doc.getElementById('question-form'));
    const startup=page(new Map(storage),{sessionDenied:denied}); await startup.run('init()'); assert.equal(startup.run('state.data'),null);
  }
  const blockedRemoval=new Map(storage); blockedRemoval.delete=()=>{throw new Error('Blocked storage');};
  const authOptions={}, deniedUi=page(blockedRemoval,authOptions); await deniedUi.run('init()'); authOptions.denied=403;
  deniedUi.location.hash='#/feed'; await deniedUi.run('renderRoute()'); assert.equal(deniedUi.run('state.profile'),null);
  assert.match(notices(deniedUi),/не удалось.*удалить/i,'A blocked removal must not be reported as successful');

  // A previous authenticated bootstrap must not replace a newer person or rights snapshot.
  let releaseOld, beganOld; const oldCompanies=new Promise(resolve=>{releaseOld=resolve;}), started=new Promise(resolve=>{beganOld=resolve;}); let sessions=0, companyReads=0;
  const raceOptions={sessionResponse:()=>response(200,{personId:++sessions===1 ? 'person-A' : 'person-B'}),
    companiesResponse:()=>{ if(++companyReads===1){beganOld(); return oldCompanies;} return response(200,[{companyId:'U_1',roles:['cfo']}]); }};
  const racing=page(new Map(storage),raceOptions), oldInit=racing.run('init()'); await started;
  await racing.run('init()'); assert.equal(racing.run('state.data.person_id'),'person-B');
  releaseOld(response(200,[{companyId:'U_1'},{companyId:'U_2'}])); await oldInit;
  assert.equal(racing.run('state.data.person_id'),'person-B','Late bootstrap must not restore a previous person');
  assert.deepEqual(context(racing),{company_id:'U_1',role:'cfo'},'Late bootstrap must not restore revoked company/role choices');
  assert.equal(racing.run('state.data.companies.length'),1);
  assert.equal(racing.doc.getElementById('error').hidden,true,'Cancelled bootstrap must not replace the current screen with a retry error');
  assert.equal(racing.requests.find(item=>item.url.includes('/companies?')).options.signal.aborted,true,'A newer bootstrap cancels the previous request');
  for (const staleStatus of [503,401,403]) {
    let release, began, reads=0, people=0;
    const pending=new Promise(resolve=>{release=resolve;}), entered=new Promise(resolve=>{began=resolve;});
    const ui=page(new Map(storage),{sessionResponse:()=>response(200,{personId:++people===1?'person-A':'person-B'}),
      companiesResponse:()=>{if(++reads===1){began();return pending;}return response(200,[{companyId:'U_1'}]);}});
    const previous=ui.run('init()'); await entered; await ui.run('init()');
    release(response(staleStatus,{})); await previous;
    if (staleStatus===503) {
      assert.equal(ui.run('state.data.person_id'),'person-B'); assert.equal(ui.doc.getElementById('error').hidden,true);
    } else {
      assert.equal(ui.run('state.data'),null); assert.equal(ui.run('state.profile'),null);
      assert.equal(ui.doc.getElementById('error').hidden,false,'Late authorization failures retain the explicit access-reset policy');
    }
  }
  console.log('PASS: per-person profile reload with feedback/QA, fresh authorization validation, identity isolation, storage errors, access reset, and zero analysis POST.');
}
module.exports={main};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
