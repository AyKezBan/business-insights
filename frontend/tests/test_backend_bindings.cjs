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
const claim = (text, kind='inference', origin='MODEL_INFERENCE', sourceIds=['news:1']) => ({text, kind, origin, sourceIds, profileFeatures:['revenueRub']});
const card = {insightId:id, companyId:'U_1', role:'cfo', eventId:'news:event:1', status:'LIMITED', mlStatus:'limited', headline:'Решение по ставке',
  whatHappened:[claim('Факт один','fact','FACT'),claim('Факт два','fact','FACT')],
  whyItMatters:[claim('Значимость')], consequences:[claim('Последствие один'),claim('Последствие два')], risks:[claim('Риск','hypothesis','HYPOTHESIS')], opportunities:[], scenarios:[],
  actions:[claim('Проверить договор','recommendation','MODEL_RECOMMENDATION')], bankView:[claim('Оценка банка','assessment','BANK_ANALYSIS',['bank:2'])], modelVsBank:[],
  sources:[{sourceId:'news:1',kind:'news',title:'Новость',url:'https://example.org/news'}, {sourceId:'bank:2',kind:'bank',title:'Отчёт',page:3,url:'https://example.org/report.pdf'}],
  missingData:['Нет срока договора'], warnings:['Данные ограничены'], generatedAt:'2026-10-25T12:00:00Z', disclaimer:'Не является рекомендацией', hidden:true};
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
    if(url.endsWith('/questions')) return {operationId:'question-1',parentInsightId:id,status:'NO_RESULT'};
    if(url==='/api/v1/answers/question-1') return {operationId:'question-1',insightId:id,status:'NO_RESULT',mlStatus:'no_answer',text:'Недостаточно данных',sources:[],missingData:['Нет договора'],warnings:[]};
    if(['/state','/feedback','/opened'].some(s=>url.endsWith(s))) return null;
    throw new Error('Unexpected endpoint '+url);
  };
  const opts={transport, requestId:'stable-question'};
  const bootstrap=await bindings.bootstrap({},opts);
  assert.equal(bootstrap.profile.company_id,'U_1'); assert.equal(bootstrap.profile.name,'F_1'); assert.equal(bootstrap.companies[0].sector,'Торговля');
  const preferences=await bindings.preferences(ctx,opts); assert.equal(preferences[`U_1:cfo:${id}`].hidden,true);
  const feed=await bindings.feed(ctx,opts); assert.equal(feed.items[0].id,id); assert.equal(feed.items[0].insight_id,id);
  assert(calls.some(c=>c.url.includes('includeHidden=true')));
  const mapped=await bindings.insight(ctx,opts);
  assert.equal(mapped.outcome,'LIMITED'); assert.equal(mapped.sources[1].id,'bank:2'); assert.equal(mapped.sources[1].page,3);
  assert(mapped.missing_data.includes('Данные ограничены')); assert(mapped.missing_data.includes('Нет срока договора'));
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
  assert.equal(calls.find(c=>c.url.endsWith('/questions')).requestId,'stable-question');
  assert(!calls.some(c=>c.url==='/api/v1/analyses' || c.url==='/api/v1/events'));
  let resumeCalls=0;
  const waiting=new Error('pending'); waiting.resume=async()=>{resumeCalls++;return {operationId:'question-1',parentInsightId:id,status:'NO_RESULT'};};
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
  const uiBindings={...bindings,bootstrap:async()=>bootstrap,preferences:async input=>bindings.preferences(input,opts),feed:async input=>bindings.feed(input,opts),
    insight:async()=>mapped,opened:async()=>{},savePreference:async input=>bindings.savePreference(input,opts),clarify:async input=>bindings.clarify(input,opts)};
  ui.run('window.INSIGHTS_CONFIG={mode:"server",bindings:{}}');ui.run('window.INSIGHTS_CONFIG').bindings=uiBindings;
  await ui.run('init()');
  for(const text of ['Факт один','Факт два','Последствие один','Последствие два','Оценка банка']) assert(ui.text().includes(text),text+' must stay visible');
  await ui.run('ask("Сколько?")'); assert(ui.doc.getElementById('reply').textContent.includes('Нет договора'),'Answer missing-data warning must be shown');
  ui.location.hash='#/feed'; await ui.run('renderRoute()');
  await walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Скрыть').onclick();
  assert(ui.text().includes('Скрыто событий: 1'));
  await walk(ui.root).find(e=>e.tagName==='button' && e.textContent==='Показать скрытые').onclick();
  assert(walk(ui.root).some(e=>e.tagName==='button' && e.textContent==='Скрыть'));
  console.log('PASS: Java DTOs, context isolation, all claims and sources, hidden/feedback, async answer continuation, errors, and zero automatic generation.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
