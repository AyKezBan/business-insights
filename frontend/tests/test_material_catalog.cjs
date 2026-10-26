'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {setup,walk}=require('./test_frontend.cjs');
const web=path.resolve(__dirname,'../web');
const integration=fs.readFileSync(path.join(web,'integration.js'),'utf8');
const bindingSource=fs.readFileSync(path.join(web,'backend-bindings.js'),'utf8');
const sandbox={URL,URLSearchParams,setTimeout,clearTimeout,AbortController};
vm.createContext(sandbox); vm.runInContext(integration+'\n'+bindingSource,sandbox);
const revision='1'.repeat(64), hash='a'.repeat(64), publicId='11111111-1111-4111-8111-111111111111', operationId='22222222-2222-4222-8222-222222222222';
const token={storeId:'store-1',generation:2,revision};
const ctx={company_id:'U_1',role:'cfo'};
const material=n=>({eventId:'opaque:event:'+n,title:'Материал '+n,text:'Подтверждённый исходный текст.',publishedAt:null,publishedDate:'2026-10-20',publishedPrecision:'date',
  source:{sourceId:'opaque:source:'+n,kind:'news',title:'Источник '+n,url:'https://example.org/article/'+n,publisherId:'fns',publishedDate:'2026-10-20',publishedPrecision:'date',materialType:'publisher_annotation',completeness:'partial',excerpt:'Подтверждённый исходный текст.'},
  relatedSources:[],topics:['tax'],conflicts:[],eventContentSha256:hash,corpusRevision:revision});
const page=(items,offset=0,total=items.length,catalogToken=token)=>({catalogToken,items,limit:100,offset,total});
const card=(changes={})=>({insightId:publicId,companyId:'U_1',role:'cfo',eventId:'opaque:event:0',status:'SUCCEEDED',mlStatus:'complete',outcome:'full',headline:'Готовый инсайт',
  whatHappened:[],whyItMatters:[],consequences:[],risks:[],opportunities:[],actions:[],bankView:[],scenarios:[],modelVsBank:[],sources:[],missingData:[],warnings:[],
  generatedAt:'2026-10-26T10:00:00Z',corpusRevision:revision,eventContentSha256:hash,...changes});
const selected=()=>({...ctx,event_id:'opaque:event:0',corpus_revision:revision,event_content_sha256:hash,request_id:'analysis-request-1',user_provided:{note:'исходные данные'}});
const response=(status,data,headers={})=>({status,ok:status<400,json:async()=>data,headers:{get:key=>headers[key] || null}});
async function main(){
  const bindings=sandbox.InsightsBackend.createBindings();
  assert.equal(typeof bindings.materialCatalog,'function','The existing Java adapter must expose a separate read-only catalog');
  assert.equal(typeof bindings.analyze,'function','Analysis must be an explicit adapter action');
  const calls=[];
  const first=Array.from({length:100},(_,n)=>material(n));
  const catalog=await bindings.materialCatalog(ctx,{transport:async(url,options={})=>{calls.push({url,...options});return calls.length===1?page(first,0,101):page([material(100)],100,101);}});
  assert.equal(catalog.items.length,101); assert.equal(catalog.catalog_token.revision,revision);
  assert.equal(catalog.items[0].event_id,'opaque:event:0'); assert.equal(catalog.items[0].source.publisher_id,'fns');
  assert.equal(catalog.items[0].event_content_sha256,hash); assert.equal(catalog.items[0].published_precision,'date');
  assert.equal(new URL(calls[1].url,'https://test.local').searchParams.get('revision'),revision);
  assert.equal(new URL(calls[1].url,'https://test.local').searchParams.get('offset'),'100');
  assert(calls.every(c=>!c.method || c.method==='GET'),'Reading a catalog must never post an analysis');
  for(const second of [page([material(100)],100,101,{...token,generation:3}),page([material(100)],100,101,{...token,storeId:'store-2'}),page([material(0)],100,101),page([],100,101),page([material(100)],99,101),page([material(100)],100,102)]){
    let n=0; await assert.rejects(bindings.materialCatalog(ctx,{transport:async()=>++n===1?page(first,0,101):second}),/каталог|материал|верси|повтор|страниц/i);
  }
  for(const invalid of [page([material(0),material(0)]),page([{...material(0),corpusRevision:'2'.repeat(64)}]),page([{...material(0),eventContentSha256:null}])]){
    await assert.rejects(bindings.materialCatalog(ctx,{transport:async()=>invalid}),/каталог|материал|верси|повтор/i);
  }
  const input=selected(), submitted=[]; let complete;
  const held=new Promise(resolve=>{complete=resolve;});
  const analysis=bindings.analyze(input,{transport:async(url,options={})=>{
    submitted.push({url,...options});
    if(url==='/api/v1/analyses') return held;
    return card();
  }});
  input.corpus_revision='2'.repeat(64); input.event_id='changed';input.company_id='U_2';input.user_provided.note='changed';
  complete({operationId,kind:'ANALYSIS',insightId:publicId,status:'SUCCEEDED',corpusRevision:revision});
  const result=await analysis;
  assert.equal(result.insight_id,publicId);assert.equal(result.context.event_id,publicId); assert.equal(result.event_id,'opaque:event:0');
  assert.equal(submitted[0].body.companyId,'U_1');assert.equal(submitted[0].body.corpusRevision,revision);assert.equal(submitted[0].body.eventId,'opaque:event:0');
  assert.equal(submitted[0].body.userProvided.note,'исходные данные'); assert.equal(submitted[0].body.requestId,'analysis-request-1');assert.equal(submitted[0].requestId,'analysis-request-1');
  for(const change of [{companyId:'U_2'},{role:'ceo'},{eventId:'other'},{corpusRevision:'2'.repeat(64)},{eventContentSha256:'b'.repeat(64)},{insightId:'logical:id'}]){
    await assert.rejects(bindings.analyze(selected(),{transport:async url=>url==='/api/v1/analyses'?{operationId,kind:'ANALYSIS',insightId:publicId,status:'SUCCEEDED'}:card(change)}),/контекст|верси|карточк|материал/i);
  }
  const limited=await bindings.analyze(selected(),{transport:async url=>url==='/api/v1/analyses'?{operationId,kind:'ANALYSIS',insightId:publicId,status:'LIMITED'}:card({status:'LIMITED',outcome:'limited'})});
  assert.equal(limited.outcome,'LIMITED');
  for(const [status,outcome,expected] of [['NO_RESULT','refused','REFUSED'],['NO_RESULT','none','NO_RESULT'],['INSUFFICIENT_DATA','none','NO_RESULT'],['FAILED','none','FAILED']]){
    const terminalCalls=[];
    const terminal=await bindings.analyze(selected(),{transport:async url=>{
      terminalCalls.push(url);assert.equal(url,'/api/v1/analyses','Terminal operation must not fetch an unpublished insight');
      return {operationId,kind:'ANALYSIS',insightId:null,status,outcome,warnings:['Нет проверенных оснований'],error:{code:'no_evidence',message:'Недостаточно источников'}};
    }});
    assert.equal(terminal.outcome,expected);assert.equal(terminal.insight_id,null);assert.equal(terminal.error_code,'no_evidence');
    assert.equal(terminal.operation_id,operationId);assert(terminal.missing_data.includes('Нет проверенных оснований'));assert.equal(terminalCalls.length,1);
  }
  for(const change of [{kind:'QUESTION'},{kind:null},{requestId:'another-request'},{operationId:'logical-id'},{corpusRevision:'2'.repeat(64)}]){
    await assert.rejects(bindings.analyze(selected(),{transport:async()=>({operationId,kind:'ANALYSIS',status:'SUCCEEDED',insightId:publicId,...change})}),/операц|запрос|контекст|верси/i);
  }
  const requests=[]; const original=new AbortController();
  const transport=sandbox.InsightsIntegration.createTransport({origin:'https://test.local',maxWaitMs:0,fetch:async(url,options)=>{
    requests.push({url,...options});
    if(options.method==='POST') return response(202,{status:'QUEUED'},{Location:'/api/v1/operations/one','Retry-After':'1'});
    return url.endsWith('/operations/one')?response(200,{operationId,kind:'ANALYSIS',insightId:publicId,status:'SUCCEEDED'}):response(200,card());
  }});
  let pending;try{await bindings.analyze(selected(),{transport,signal:original.signal});}catch(e){pending=e;}
  assert.equal(typeof pending?.resume,'function');original.abort();
  assert.equal((await pending.resume(new AbortController().signal)).insight_id,publicId);
  assert.deepEqual(requests.map(r=>r.method),['POST','GET','GET'],'Resume must never POST another analysis');
  const aborted=new AbortController(), abortRequests=[];
  const abortTransport=sandbox.InsightsIntegration.createTransport({origin:'https://test.local',fetch:async(url,options)=>{
    abortRequests.push(options.method);
    if(options.method==='POST')return response(202,{status:'RUNNING'},{Location:'/api/v1/operations/aborted'});
    return url.endsWith('/operations/aborted')?response(200,{operationId,kind:'ANALYSIS',insightId:publicId,status:'SUCCEEDED'}):response(200,card());
  },sleep:async()=>{aborted.abort();}});
  let interrupted;try{await bindings.analyze(selected(),{transport:abortTransport,signal:aborted.signal});}catch(e){interrupted=e;}
  assert.equal(typeof interrupted?.resume,'function','A known operation remains resumable when navigation aborts polling');
  assert.equal((await interrupted.resume(new AbortController().signal)).insight_id,publicId);
  assert.deepEqual(abortRequests,['POST','GET','GET']);
  const fetchRetryCalls=[];let resultFetches=0;
  const resultTransport=async(url,options={})=>{
    fetchRetryCalls.push({url,method:options.method});
    if(url==='/api/v1/analyses')return {operationId,kind:'ANALYSIS',insightId:publicId,status:'SUCCEEDED'};
    if(++resultFetches===1){const error=new Error('Result unavailable');error.status=503;throw error;}
    return card();
  };
  let resultPending;try{await bindings.analyze(selected(),{transport:resultTransport});}catch(error){resultPending=error;}
  assert.equal(typeof resultPending?.resume,'function','A completed operation retries only the result read');
  assert.equal((await resultPending.resume()).insight_id,publicId);
  assert.deepEqual(fetchRetryCalls.map(call=>call.method),['POST','GET','GET']);
  const uiRequests=[];let failCatalog=false, conflict=false, emptyCatalog=false;
  const appSource=integration+'\n'+bindingSource+'\n'+fs.readFileSync(path.join(web,'app.js'),'utf8');
  const ui=setup(appSource,'',new Map(),'#/materials',async(url,options)=>{
    const parsed=new URL(url,'https://test.local'), endpoint=parsed.pathname;uiRequests.push({url,endpoint,...options});
    if(endpoint==='/api/v1/session')return response(200,{personId:'person-1'});
    if(endpoint==='/api/v1/companies')return response(200,[{companyId:'U_1',okvedName:'Торговля'}]);
    if(endpoint==='/api/v1/materials/events')return failCatalog?response(503,{message:'Обновление каталога недоступно'}):response(200,page(emptyCatalog?[]:[material(0)]));
    if(endpoint==='/api/v1/companies/U_1/insights')return response(200,{items:[card()]});
    if(endpoint==='/api/v1/analyses')return conflict?response(409,{message:'Выбранная версия устарела'}):response(202,{status:'QUEUED'},{Location:'/api/v1/operations/ui','Retry-After':'0'});
    if(endpoint==='/api/v1/operations/ui')return response(200,{operationId,kind:'ANALYSIS',insightId:publicId,status:'SUCCEEDED'});
    if(endpoint===`/api/v1/insights/${publicId}`)return response(200,card());
    if(endpoint.endsWith('/opened'))return response(204,null);
    throw new Error('Unexpected UI endpoint '+endpoint);
  });
  ui.run('location.origin="https://test.local";window.INSIGHTS_CONFIG={mode:"server",bindings:InsightsBackend.createBindings()}');
  await ui.run('init()');
  assert(ui.text().includes('Доступные материалы'));assert(ui.text().includes('Материал 0'));assert(ui.text().includes('аннотация'));
  assert.equal(uiRequests.filter(r=>r.method==='POST').length,0,'Loading and displaying materials never starts analysis');
  emptyCatalog=true;await ui.run('renderRoute()');
  assert(ui.text().includes('пока нет материалов'));assert(!ui.doc.getElementById('status').textContent.includes('Загружаем'),'An empty published catalog has finished loading');
  emptyCatalog=false;
  failCatalog=true;ui.location.hash='#/feed';await ui.run('renderRoute()');assert(ui.text().includes('Готовый инсайт'),'Prepared results do not depend on refreshing the catalog');
  ui.location.hash='#/materials';await ui.run('renderRoute()');assert(ui.doc.getElementById('error').textContent.includes('Обновление каталога недоступно'));assert(ui.text().includes('Мои инсайты'));
  failCatalog=false;await ui.run('renderRoute()');conflict=true;
  await walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Проанализировать').onclick();
  const firstPost=uiRequests.find(r=>r.endpoint==='/api/v1/analyses');
  assert.equal(JSON.parse(firstPost.body).corpusRevision,revision); assert.equal(JSON.parse(firstPost.body).eventContentSha256,hash);
  assert(ui.doc.getElementById('error').textContent.includes('Выбранная версия'));
  const catalogReads=uiRequests.filter(r=>r.endpoint==='/api/v1/materials/events').length;
  assert.equal(ui.location.hash.split('?')[0],'#/materials','Conflict must not navigate to a replacement result');
  conflict=false;await walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Повторить').onclick();
  const posts=uiRequests.filter(r=>r.endpoint==='/api/v1/analyses');
  assert.equal(posts[0].body,posts[1].body,'Explicit retry preserves the selected snapshot and request identity');
  assert.equal(posts[0].headers['Idempotency-Key'],posts[1].headers['Idempotency-Key']);
  assert.equal(uiRequests.filter(r=>r.endpoint==='/api/v1/materials/events').length,catalogReads,'409 must not trigger automatic refresh/reselection');
  assert.equal(ui.location.hash.split('?')[0],'#/insight/'+publicId);
  let releaseLate;
  const lateResult=new Promise(resolve=>{releaseLate=resolve;});
  const late=setup(appSource,'',new Map(),'#/materials');late.run('location.origin="https://test.local"');
  const lateBindings={bootstrap:async()=>({person_id:'person-1',profile:{name:'User',company_id:'U_1',role:'cfo'},companies:[{id:'U_1',name:'Компания'}],roles:{cfo:'CFO',ceo:'CEO'},views:{overview:'Обзор'}}),
    preferences:async()=>({}),feed:async input=>({context:{company_id:input.company_id,role:input.role},items:[]}),insight:async()=>{},clarify:async()=>{},savePreference:async()=>{},opened:async()=>{},
    materialCatalog:async input=>({...catalog,context:{company_id:input.company_id,role:input.role},items:[catalog.items[0]]}),analyze:async()=>lateResult};
  late.run('window.INSIGHTS_CONFIG={mode:"server",bindings:{}}');late.run('window.INSIGHTS_CONFIG').bindings=lateBindings;
  await late.run('init()');const lateClick=walk(late.root).find(e=>e.tagName==='button' && e.textContent==='Проанализировать').onclick();
  late.run("state.profile={...state.profile,role:'ceo'}");late.location.hash='#/feed';await late.run('renderRoute()');
  releaseLate(result);await lateClick;assert.equal(late.location.hash.split('?')[0],'#/feed','An old company/role request must not navigate a newer screen');
  let terminalSubmissions=0;
  const terminalUi=setup(appSource,'',new Map(),'#/materials');terminalUi.run('location.origin="https://test.local"');
  const terminalBindings={...lateBindings,analyze:async()=>{terminalSubmissions++;return {insight_id:null,operation_id:operationId,outcome:'FAILED',outcome_message:'Недостаточно проверенных источников',missing_data:[]};}};
  terminalUi.run('window.INSIGHTS_CONFIG={mode:"server",bindings:{}}');terminalUi.run('window.INSIGHTS_CONFIG').bindings=terminalBindings;
  await terminalUi.run('init()');const terminalButton=walk(terminalUi.root).find(e=>e.tagName==='button' && e.textContent==='Проанализировать');
  await terminalButton.onclick();assert.equal(terminalUi.location.hash.split('?')[0],'#/materials','Unpublished terminal result must not navigate to an insight');
  assert(terminalUi.text().includes('Недостаточно проверенных источников'));assert.equal(terminalButton.disabled,true);
  await terminalButton.onclick();assert.equal(terminalSubmissions,1,'A terminal result cannot submit the same analysis again');
  const demo=setup(appSource,'',new Map(),'#/materials',async url=>{if(url==='/api/bootstrap')return {ok:true,json:async()=>({profile:{name:'Demo',company_id:'demo',role:'cfo'},companies:[{id:'demo',name:'Demo'}],roles:{cfo:'CFO'},views:{overview:'Обзор'}})};throw new Error('Demo catalog must not call '+url);});
  await demo.run('init()');assert(demo.text().includes('серверном режиме'),'Demo catalog is explicitly unsupported');assert.equal(demo.requests.length,1);
  console.log('PASS: catalog snapshot pagination/identity, explicit frozen analysis, outcome/context guards, one-POST polling/resume, independent prepared feed, conflict retry, and unsupported demo.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
