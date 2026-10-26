'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {setup, walk} = require('./test_frontend.cjs');
const web = path.resolve(__dirname, '../web');
const sandbox = {URL, URLSearchParams, setTimeout, clearTimeout, AbortController};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(web, 'integration.js'), 'utf8'), sandbox);
const bindingsPath = path.join(web, 'backend-bindings.js');
if (fs.existsSync(bindingsPath)) vm.runInContext(fs.readFileSync(bindingsPath, 'utf8'), sandbox);
const id = '11111111-1111-4111-8111-111111111111';
const claim = (text, kind='inference', origin='MODEL_INFERENCE', sourceIds=['news:1']) => ({claimId:'claim:'+text, text, kind, origin, sourceIds, profileFeatures:['revenueRub'], level:'medium', horizon:'до конца года'});
const card = {insightId:id, companyId:'U_1', role:'cfo', eventId:'news:event:1', status:'LIMITED', mlStatus:'limited', outcome:'limited', mlInsightId:'ml:U_1:news:event:1:run-1', runId:'run-1', version:2, requestId:'backend-request-1', parentRunId:'run-0', cacheHit:false, asOf:'2026-10-25T09:00:00Z', corpusRevision:'1111111111111111111111111111111111111111111111111111111111111111', eventContentSha256:'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', errorCode:null, conflicts:[{reason:'Источники приводят разные оценки',sourceIds:['news:1','news:conflict'],resolved:false}], headline:'Решение по ставке',
  whatHappened:[claim('Факт один','fact','FACT'),claim('Факт два','fact','FACT')],
  whyItMatters:[claim('Значимость')], consequences:[claim('Последствие один'),claim('Последствие два')], risks:[claim('Риск','hypothesis','HYPOTHESIS')], opportunities:[], scenarios:[],
  actions:[claim('Проверить договор','recommendation','MODEL_RECOMMENDATION')], bankView:[claim('Оценка банка','assessment','BANK_ANALYSIS',['bank:2'])], modelVsBank:[],
  sources:[{sourceId:'news:1',kind:'news',title:'Новость',url:'https://example.org/news',docId:'article-1',contentSha256:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',excerpt:'В исходном материале сохранено условие.',publisherId:'eia',adapterId:'eia_v1',materialType:'publisher_annotation',completeness:'partial',language:'en',publishedDate:'2026-10-20',publishedPrecision:'date',rawPublication:'2026-10-20',sourceTimezone:'UTC',sourceUtcOffsetSeconds:0,timezoneProvenance:'explicit_offset',firstSeenAt:'2026-10-21T10:00:00Z',retrievedAt:'2026-10-22T11:00:00Z',rightsUrl:'https://example.org/rights',translationAttribution:'Перевод с английского; источник EIA'}, {sourceId:'bank:2',kind:'bank',title:'Отчёт',page:3,url:'https://example.org/report.pdf?revision=1111111111111111111111111111111111111111111111111111111111111111',docId:'bank-doc-1',contentSha256:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',excerpt:'При сохранении условий банк ожидает замедления спроса.',statementType:'forecast',targetPeriod:'до конца 2026 года',validUntil:'2026-12-31'}, {sourceId:'news:conflict',kind:'news',title:'Другой источник',url:'https://example.org/conflicting',excerpt:'Второй источник приводит другую оценку.'}],
  missingData:['Нет срока договора'], warnings:['Данные ограничены'], generatedAt:'2026-10-25T12:00:00Z', disclaimer:'Не является рекомендацией', hidden:true, feedback:{rating:'UNSUPPORTED',comment:'Есть ошибка'}, openedAt:'2026-10-25T12:30:00Z'};
const ctx = {company_id:'U_1', role:'cfo', event_id:id, view:'overview'};
async function main() {
  assert.equal(typeof sandbox.InsightsBackend?.createBindings, 'function', 'Concrete Java bindings factory must be exported');
  const bindings = sandbox.InsightsBackend.createBindings();
  const calls=[];
  const transport=async (url, options={}) => {
    calls.push({url, ...options});
    if(url==='/api/v1/session') return {personId:'F_1',authentication:'DEMO_BEARER'};
    if(url.startsWith('/api/v1/companies?')) return [{companyId:'U_1',okvedName:'Торговля'}];
    if(url.startsWith('/api/v1/companies/U_1/insights?')) return {items:[card],emptyReason:null};
    if(url===`/api/v1/insights/${id}`) return card;
    if(url.endsWith('/questions')) return {operationId:'question-1',parentInsightId:id,status:'NO_RESULT',outcome:'none',requestId:'backend-question-1',corpusRevision:'1111111111111111111111111111111111111111111111111111111111111111'};
    if(url==='/api/v1/answers/question-1') return {operationId:'question-1',insightId:id,status:'NO_RESULT',mlStatus:'no_answer',mlInsightId:'ml:U_1:news:event:1:run-1',runId:'qa-run-1',companyId:'U_1',eventId:'news:event:1',asOf:'2026-10-25T09:00:00Z',corpusRevision:'1111111111111111111111111111111111111111111111111111111111111111',eventContentSha256:'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',text:'Недостаточно данных',claims:[claim('Подтверждённый факт ответа','fact','FACT')],sources:card.sources,missingData:['Нет договора'],warnings:[]};
    if(['/state','/feedback','/opened'].some(s=>url.endsWith(s))) return null;
    throw new Error('Unexpected endpoint '+url);
  };
  const opts={transport, requestId:'stable-question'};
  const bootstrap=await bindings.bootstrap({},opts);
  assert.equal(bootstrap.profile.company_id,'U_1'); assert.equal(bootstrap.profile.name,'F_1'); assert.equal(bootstrap.companies[0].sector,'Торговля');
  const preferences=await bindings.preferences(ctx,opts); assert.equal(preferences[`U_1:cfo:${id}`].hidden,true);
  const feedbackKey=`U_1:cfo:${id}:${id}`;
  assert.equal(preferences[feedbackKey]?.feedback,'not_useful','Persisted feedback must be restored without a local write');
  assert.equal(preferences[feedbackKey]?.reason,'error','The fixed comment distinguishes an error from an unclear basis');
  for(const [rating,comment,expected,reason] of [['USEFUL',null,'useful',null],['IRRELEVANT','Не относится к компании','not_useful','company'],['UNSUPPORTED','Неясно основание','not_useful','basis'],['UNSUPPORTED','Есть ошибка','not_useful','error'],['OTHER','Не помогает принять решение','not_useful','decision'],['OTHER','Другое','not_useful','other'],['OTHER',null,'not_useful',null],['OTHER','constructor','not_useful',null]]) {
    const restarted=sandbox.InsightsBackend.createBindings();
    const restored=await restarted.preferences(ctx,{transport:async()=>({items:[{...card,feedback:{rating,comment}}]})});
    assert.equal(restored[feedbackKey]?.feedback,expected); assert.equal(restored[feedbackKey]?.reason,reason);
  }
  const refreshing=sandbox.InsightsBackend.createBindings();
  let persisted={rating:'UNSUPPORTED',comment:'Есть ошибка'}, failWrite=false;
  const persistenceTransport=async(url,options={})=>{
    if(url.includes('/insights?')) return {items:[JSON.parse(JSON.stringify({...card,feedback:persisted}))]};
    if(url===`/api/v1/insights/${id}/feedback` && options.method==='PUT') {if(failWrite) throw new Error('write failed');return null;}
    throw new Error('Unexpected feedback endpoint '+url);
  };
  await refreshing.preferences(ctx,{transport:persistenceTransport});
  await refreshing.savePreference({context:ctx,insight_id:id,value:{feedback:'useful'}},{transport:persistenceTransport});
  assert.equal((await refreshing.preferences(ctx,{transport:persistenceTransport}))[feedbackKey]?.reason,'error','Fresh server data must replace a previously successful local value');
  persisted=null;
  assert.equal((await refreshing.preferences(ctx,{transport:persistenceTransport}))[feedbackKey],undefined,'An explicit null clears stale local feedback');
  persisted={rating:'USEFUL',comment:null}; failWrite=true;
  await assert.rejects(refreshing.savePreference({context:ctx,insight_id:id,value:{feedback:'not_useful',reason:'basis'}},{transport:persistenceTransport}),/write failed/);
  assert.equal((await refreshing.preferences(ctx,{transport:persistenceTransport}))[feedbackKey]?.feedback,'useful','Failed write cannot replace the persisted rating');

  const feed=await bindings.feed(ctx,opts); assert.equal(feed.items[0].id,id); assert.equal(feed.items[0].insight_id,id);
  assert(calls.some(c=>c.url.includes('includeHidden=true')));
  const mapped=await bindings.insight(ctx,opts);
  assert.equal(mapped.outcome,'LIMITED'); assert.equal(mapped.sources[1].id,'bank:2'); assert.equal(mapped.sources[1].page,3);
  assert(mapped.missing_data.includes('Данные ограничены')); assert(mapped.missing_data.includes('Нет срока договора'));
  // Real Java-shaped metadata must remain usable by provenance/trace UI after mapping.
  const evidence=mapped.sources[0];
  assert.equal(evidence.excerpt,'В исходном материале сохранено условие.');
  assert.equal(evidence.doc_id,'article-1'); assert.equal(evidence.content_sha256,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
  assert.equal(evidence.publisher_id,'eia'); assert.equal(evidence.adapter_id,'eia_v1');
  assert.equal(evidence.material_type,'publisher_annotation'); assert.equal(evidence.completeness,'partial'); assert.equal(evidence.language,'en');
  assert.equal(evidence.published_date,'2026-10-20'); assert.equal(evidence.published_precision,'date');
  assert.equal(evidence.raw_publication,'2026-10-20'); assert.equal(evidence.source_timezone,'UTC');
  assert.equal(evidence.source_utc_offset_seconds,0); assert.equal(evidence.timezone_provenance,'explicit_offset');
  assert.equal(evidence.first_seen_at,'2026-10-21T10:00:00Z'); assert.equal(evidence.retrieved_at,'2026-10-22T11:00:00Z');
  assert.equal(evidence.rights_url,'https://example.org/rights'); assert.equal(evidence.translation_attribution,'Перевод с английского; источник EIA');
  assert.equal(mapped.sources[1].statement_type,'forecast'); assert.equal(mapped.sources[1].target_period,'до конца 2026 года'); assert.equal(mapped.sources[1].valid_until,'2026-12-31');
  assert.equal(mapped.ml_insight_id,'ml:U_1:news:event:1:run-1'); assert.equal(mapped.insight_id,id); assert.equal(mapped.event_id,'news:event:1');
  assert.equal(mapped.opened_at,'2026-10-25T12:30:00Z');
  assert.equal(mapped.run_id,'run-1'); assert.equal(mapped.version,2); assert.equal(mapped.request_id,'backend-request-1');
  assert.equal(mapped.parent_run_id,'run-0'); assert.equal(mapped.cache_hit,false); assert.equal(mapped.as_of,'2026-10-25T09:00:00Z');
  assert.equal(mapped.corpus_revision,'1111111111111111111111111111111111111111111111111111111111111111'); assert.equal(mapped.event_content_sha256,'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'); assert.equal(mapped.ml_outcome,'limited');
  assert.equal(mapped.conflicts[0].source_ids[1],'news:conflict'); assert.equal(mapped.conflicts[0].resolved,false);
  assert.equal(mapped.blocks[0].claims[0].claim_id,'claim:Факт один');
  assert.equal(mapped.blocks[0].claims[0].level,'medium'); assert.equal(mapped.blocks[0].claims[0].horizon,'до конца года');
  assert.equal(sandbox.InsightsIntegration.sourceURL(mapped.sources[1],false),'https://example.org/report.pdf?revision=1111111111111111111111111111111111111111111111111111111111111111#page=3');
  for(const [status,mlOutcome,expected] of [['SUCCEEDED','full','COMPLETE'],['SUCCEEDED','refused','REFUSED'],['LIMITED','none','NO_RESULT'],['FAILED','none','FAILED']]) {
    const terminal=await bindings.insight(ctx,{transport:async()=>({...card,status,outcome:mlOutcome,errorCode:'insufficient_evidence'})});
    assert.equal(terminal.outcome,expected,'ML outcome must not be replaced by operation status');
    assert.equal(terminal.error_code,'insufficient_evidence');
  }
  await assert.rejects(bindings.insight(ctx,{transport:async()=>({...card,outcome:'unexpected'})}),/исход/);
  await bindings.insight(ctx,opts); // Restore the actual pinned card used by QA.

  assert.equal(mapped.blocks.find(b=>b.kind==='fact').claims.length,2);
  assert(mapped.blocks.find(b=>b.kind==='ai').sections.some(p=>p.text==='Последствие два' && p.source_ids[0]==='news:1' && p.feature_refs[0]==='revenueRub'));
  await assert.rejects(bindings.insight({...ctx,company_id:'U_2'},opts),/контекст/);
  await bindings.savePreference({context:ctx,insight_id:id,value:{hidden:false}},opts);
  assert.equal(calls.at(-1).method,'PUT'); assert.equal(calls.at(-1).body.hidden,false);
  await bindings.savePreference({context:ctx,insight_id:id,value:{feedback:'not_useful',reason:'basis'}},opts);
  assert.equal(calls.at(-1).body.rating,'UNSUPPORTED');
  await bindings.opened({context:ctx,insight_id:id},opts); assert.equal(calls.at(-1).method,'POST');
  const answer=await bindings.clarify({...ctx,insight_id:id,question:'Сколько?',request_id:'stable-question'},opts);
  assert.equal(answer.outcome,'NO_RESULT'); assert.equal(answer.status,'no_answer'); assert.equal(answer.request_id,'stable-question');
  assert.equal(answer.operation_request_id,'backend-question-1','Backend request ID is distinct from client idempotency key');
  assert.equal(answer.ml_insight_id,'ml:U_1:news:event:1:run-1'); assert.equal(answer.run_id,'qa-run-1');
  assert.equal(answer.company_id,'U_1'); assert.equal(answer.event_id,'news:event:1');
  assert.equal(answer.as_of,'2026-10-25T09:00:00Z'); assert.equal(answer.corpus_revision,'1111111111111111111111111111111111111111111111111111111111111111'); assert.equal(answer.event_content_sha256,'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee');
  assert.equal(answer.claims[0].claim_id,'claim:Подтверждённый факт ответа'); assert.equal(answer.sources[0].content_sha256,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
  for(const change of [{companyId:'U_2'},{eventId:'different-logical-event'},{mlInsightId:'other-ml-insight'},{corpusRevision:'revision-2'}]) {
    await assert.rejects(bindings.clarify({...ctx,insight_id:id,question:'Сколько?',request_id:'stable-question'},
      {...opts,transport:async(url,o)=>{const data=await transport(url,o);return url.includes('/answers/') ? {...data,...change} : data;}}),/контекст|верси|карточке/);
  }

  assert.equal(calls.find(c=>c.url.endsWith('/questions')).requestId,'stable-question');
  assert(!calls.some(c=>c.url==='/api/v1/analyses' || c.url==='/api/v1/events'));
  let resumeCalls=0;
  const waiting=new Error('pending'); waiting.resume=async()=>{resumeCalls++;return {operationId:'question-1',parentInsightId:id,status:'NO_RESULT',outcome:'none',requestId:'backend-question-1',corpusRevision:'1111111111111111111111111111111111111111111111111111111111111111'};};
  let paused; try { await bindings.clarify({...ctx,insight_id:id,question:'Сколько?',request_id:'stable-question'}, {...opts,transport:async(url,o)=>url.endsWith('/questions') ? Promise.reject(waiting) : transport(url,o)}); } catch(e) {paused=e;}
  assert.equal((await paused.resume()).status,'no_answer'); assert.equal(resumeCalls,1);
  const originalController=new AbortController(), retryController=new AbortController(), resumedRequests=[];
  const httpResponse=(status,data,headers={})=>({status,ok:status<400,json:async()=>data,headers:{get:key=>headers[key] || null}});
  const resumedTransport=sandbox.InsightsIntegration.createTransport({origin:'https://example.org',maxWaitMs:0,fetch:async(url,options)=>{
    resumedRequests.push({url,method:options.method});
    if(options.method==='POST' && url.endsWith('/questions')) return httpResponse(202,{status:'QUEUED'},{Location:'/api/v1/operations/question-retry'});
    if(url.endsWith('/operations/question-retry')) return httpResponse(200,{operationId:'question-retry',parentInsightId:id,status:'SUCCEEDED'});
    if(url.endsWith('/answers/question-retry')) return httpResponse(200,{operationId:'question-retry',insightId:id,status:'SUCCEEDED',mlStatus:'answered',text:'Продолженный ответ',sources:[],missingData:[],warnings:[]});
    throw new Error('Unexpected resumed endpoint '+url);
  }});
  let retryable;
  try { await bindings.clarify({...ctx,insight_id:id,question:'Продолжить?',request_id:'resume-key'}, {...opts,transport:resumedTransport,signal:originalController.signal}); }
  catch(e) { retryable=e; }
  assert.equal(typeof retryable?.resume,'function');
  originalController.abort(); // app.ask aborts its previous controller before retrying.
  let resumedAnswer;
  await assert.doesNotReject(async()=>{resumedAnswer=await retryable.resume(retryController.signal);},'Answer fetch must use the fresh resume signal');
  assert.equal(resumedAnswer.text,'Продолженный ответ');
  assert.deepEqual(resumedRequests.map(r=>r.method),['POST','GET','GET'],'Resume must not submit another paid question');
  await assert.rejects(bindings.clarify({...ctx,insight_id:id,question:'Сколько?',request_id:'stable-question'},
    {...opts,transport:async()=>({operationId:'failed',parentInsightId:id,status:'FAILED',error:{message:'ML недоступен'}})}),/ML недоступен/);
  await assert.rejects(bindings.clarify({...ctx,insight_id:id,question:'Сколько?',request_id:'stable-question'},
    {...opts,transport:async()=>({operationId:'other',parentInsightId:'other',status:'SUCCEEDED'})}),/другой карточке/);
  const response={status:422,ok:false,json:async()=>({message:'Проверенная ошибка Java'}),headers:{get:()=>null}};
  await assert.rejects(sandbox.InsightsIntegration.createTransport({origin:'https://example.org',fetch:async()=>response})('/api/v1/session'),/Проверенная ошибка Java/);
  const source=fs.readFileSync(path.join(web,'integration.js'),'utf8')+'\n'+fs.readFileSync(path.join(web,'app.js'),'utf8');
  const ui=setup(source,'',new Map(),`#/insight/${id}`);
  ui.run('location.origin="https://example.org"');
  const reloadedBindings=sandbox.InsightsBackend.createBindings();
  const uiBindings={...reloadedBindings,bootstrap:async()=>bootstrap,preferences:async input=>reloadedBindings.preferences(input,opts),feed:async input=>reloadedBindings.feed(input,opts),
    insight:async input=>reloadedBindings.insight(input,opts),opened:async()=>{},savePreference:async input=>reloadedBindings.savePreference(input,opts),clarify:async input=>reloadedBindings.clarify(input,opts)};
  ui.run('window.INSIGHTS_CONFIG={mode:"server",bindings:{}}');ui.run('window.INSIGHTS_CONFIG').bindings=uiBindings;
  await ui.run('init()');
  assert.equal(walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Не полезно').getAttribute('aria-pressed'),'true','Saved rating must be selected after a new bindings/UI session');
  assert.equal(walk(ui.root).find(e=>e.tagName==='select' && e.getAttribute('aria-label')==='Причина отрицательной оценки (необязательно)').value,'basis');
  const normalInsight=uiBindings.insight;
  for(const [detailFeedback,useful] of [[null,false],[{rating:'USEFUL',comment:null},true]]) {
    uiBindings.insight=async input=>reloadedBindings.insight(input,{transport:async()=>({...card,feedback:detailFeedback})});
    await ui.run('renderRoute()');
    assert.equal(walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Не полезно').getAttribute('aria-pressed'),'false','Later detail response, including null, overrides the earlier feed feedback');
    assert.equal(walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Полезно').getAttribute('aria-pressed'),String(useful));
  }
  uiBindings.insight=normalInsight; await ui.run('renderRoute()');


  for(const text of ['Факт один','Факт два','Последствие один','Последствие два','Оценка банка']) assert(ui.text().includes(text),text+' must stay visible');
  for(const text of ['В исходном материале сохранено условие.','Перевод с английского; источник EIA','2026-10-20','до конца 2026 года','2026-12-31']) assert(ui.text().includes(text),text+' provenance must stay visible');
  assert(ui.text().includes('аннотация'),'Partial source coverage must be visible');
  assert(ui.text().includes('время не указано'),'Date-only precision must not imply a known intraday publication time');
  assert(walk(ui.root).some(e=>e.dataset.claimId==='claim:Факт один'),'Claim identity must reach its rendered evidence');
  assert(ui.text().includes('Источники расходятся'),'Unresolved conflicts must be visible independently of generic warnings');
  assert(walk(ui.root).some(e=>e.tagName==='a' && e.href==='https://example.org/conflicting'));
  assert(walk(ui.root).some(e=>e.tagName==='a' && e.href==='https://example.org/report.pdf?revision=1111111111111111111111111111111111111111111111111111111111111111#page=3'));
  assert(!ui.text().includes('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') && !ui.text().includes('eia_v1'),'Technical IDs must not crowd source labels');
  const refused=await bindings.insight(ctx,{transport:async()=>({...card,outcome:'refused',missingData:['Нет проверенных оснований'],warnings:[]})});
  ui.doc.getElementById('page').replaceChildren(); ui.run('renderCard')(refused);
  assert(ui.text().includes('Нет проверенных оснований'),'Refusal must explain the evidence gap');
  assert(!ui.text().includes('Статус результата не распознан')); assert(!ui.text().includes('Факт один'),'Refused result must not render draft claims');
  const refusedFeed=await bindings.feed(ctx,{transport:async()=>({items:[{...card,outcome:'refused',headline:'Непроверенный заголовок ленты',whyItMatters:[claim('Непроверенный вывод ленты')]}]})});
  ui.doc.getElementById('page').replaceChildren(); ui.run('renderFeed')(refusedFeed);
  assert(!ui.text().includes('Непроверенный заголовок ленты') && !ui.text().includes('Непроверенный вывод ленты'),'Refused feed entry must not expose draft content');
  assert(ui.text().includes('Недостаточно проверенных оснований'));
  ui.doc.getElementById('page').replaceChildren(); ui.run('renderCard')(mapped);
  const normalClarify=uiBindings.clarify;
  uiBindings.clarify=async input=>reloadedBindings.clarify(input,{...opts,transport:async(url,o)=>{
    const data=await transport(url,o);
    return url.endsWith('/questions') ? {...data,status:'LIMITED',outcome:'refused'} : {...data,status:'LIMITED',text:'Непроверенный ответ',claims:[claim('Непроверенное утверждение QA')],missingData:['Нет проверенного ответа']};
  }});
  await ui.run('ask("Можно ли подтвердить?")');
  assert(ui.doc.getElementById('reply').textContent.includes('Нет проверенного ответа'));
  assert(!ui.doc.getElementById('reply').textContent.includes('Непроверенный ответ') && !ui.doc.getElementById('reply').textContent.includes('Непроверенное утверждение QA'),'Refused QA must not expose draft text or claims');
  uiBindings.clarify=normalClarify;

  await ui.run('ask("Сколько?")'); assert(ui.doc.getElementById('reply').textContent.includes('Нет договора'),'Answer missing-data warning must be shown');
  ui.location.hash='#/feed'; await ui.run('renderRoute()');
  await walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Скрыть').onclick();
  assert(ui.text().includes('Скрыто событий: 1'));
  await walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Показать скрытые').onclick();
  assert(walk(ui.root).some(e=>e.tagName==='button' && e.textContent==='Скрыть'));
  await require('./test_evidence_spans.cjs').main();
  await require('./test_profile_context.cjs').main();
  console.log('PASS: Java DTOs, context isolation, all claims and sources, hidden/feedback, async answer continuation, errors, and zero automatic generation.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
