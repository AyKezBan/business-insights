'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {setup}=require('./test_frontend.cjs');
const web=path.resolve(__dirname,'../web');

async function main() {
  const sandbox={URL,URLSearchParams,setTimeout,clearTimeout,AbortController};vm.createContext(sandbox);
  for(const name of ['integration.js','backend-bindings.js'])vm.runInContext(fs.readFileSync(path.join(web,name),'utf8'),sandbox);
  const id='11111111-1111-4111-8111-111111111111', sourceId='source:🧪/opaque?version#part', text='🧪 Не изменилось.';
  const span={sourceId,contentSha256:'a'.repeat(64),start:2,end:18};
  const claim={claimId:'claim:opaque',text,kind:'fact',origin:'FACT',sourceIds:[sourceId],profileFeatures:[],evidenceSpans:[span]};
  const card={insightId:id,companyId:'U_1',role:'cfo',eventId:'event:opaque',status:'SUCCEEDED',mlStatus:'complete',outcome:'full',headline:'Synthetic spans',
    whatHappened:[claim],whyItMatters:[],consequences:[],risks:[{...claim,level:'low'}],opportunities:[],actions:[],bankView:[],scenarios:[],
    sources:[{sourceId,kind:'news',title:'Synthetic source',contentSha256:'a'.repeat(64),excerpt:'Short annotation.',url:'https://example.test/source'}],missingData:[],warnings:[]};
  const ctx={company_id:'U_1',role:'cfo',event_id:id,view:'overview'};
  const bindings=sandbox.InsightsBackend.createBindings();
  const mapped=await bindings.insight(ctx,{transport:async()=>card});
  const expected=[{source_id:sourceId,content_sha256:'a'.repeat(64),start:2,end:18}];
  assert(Array.isArray(mapped.blocks[0].claims[0].evidence_spans),'The binding must retain explicit EvidenceSpan values');
  assert.deepEqual(JSON.parse(JSON.stringify(mapped.blocks[0].claims[0].evidence_spans)),expected);
  assert.equal(mapped.blocks[0].claims[0].text,text,'Code-point offsets must not slice the already rendered ML claim');
  assert.deepEqual(JSON.parse(JSON.stringify(mapped.blocks.find(b=>b.kind==='ai').sections[0].evidence_spans)),expected);
  const answer=await bindings.clarify({...ctx,insight_id:id,question:'Synthetic?',request_id:'synthetic-question'}, {transport:async url=>url.endsWith('/questions')
    ? {operationId:'qa-1',parentInsightId:id,status:'SUCCEEDED'}
    : {operationId:'qa-1',insightId:id,status:'SUCCEEDED',mlStatus:'answered',text,claims:[claim],sources:card.sources,missingData:[],warnings:[]}});
  assert.deepEqual(JSON.parse(JSON.stringify(answer.claims[0].evidence_spans)),expected);
  assert.equal(answer.claims[0].text,text);
  const legacy={...claim};delete legacy.evidenceSpans;
  const old=await bindings.insight(ctx,{transport:async()=>({...card,whatHappened:[legacy]})});
  assert.deepEqual(JSON.parse(JSON.stringify(old.blocks[0].claims[0].evidence_spans)),[]);
  for(const invalid of [null,{},[null],[{...span,start:-1}],[{...span,end:0}],[{...span,end:2}],[{...span,end:1}],
    [{...span,start:2.5}],[{...span,start:'2'}],[{...span,end:true}],[{...span,end:Number.MAX_SAFE_INTEGER+1}],
    [{...span,sourceId:''}],[{...span,sourceId:7}],[{...span,contentSha256:'bad'}],[{...span,page:1}]]) {
    await assert.rejects(bindings.insight(ctx,{transport:async()=>({...card,whatHappened:[{...claim,evidenceSpans:invalid}]})}),/фрагмент|основан/);
  }
  await assert.rejects(bindings.clarify({...ctx,insight_id:id,question:'Synthetic?',request_id:'bad-question'}, {transport:async url=>url.endsWith('/questions')
    ? {operationId:'qa-2',parentInsightId:id,status:'SUCCEEDED'}
    : {operationId:'qa-2',insightId:id,status:'SUCCEEDED',mlStatus:'answered',text,claims:[{...claim,evidenceSpans:[{...span,end:1}]}],sources:card.sources,missingData:[],warnings:[]}}),/фрагмент|основан/);
  const ui=setup(fs.readFileSync(path.join(web,'integration.js'),'utf8')+'\n'+fs.readFileSync(path.join(web,'app.js'),'utf8'),'',new Map(),'#/home');
  ui.run('state.data={companies:[{id:"U_1",name:"Synthetic company"}],roles:{cfo:"CFO"},views:{overview:"Overview"}}; state.ctx={company_id:"U_1",role:"cfo",view:"overview"}; state.profile={company_id:"U_1",role:"cfo"}');
  ui.doc.getElementById('page').replaceChildren();ui.run('renderCard')(mapped);
  assert(ui.text().includes(text),'Astral Unicode claim remains intact in the rendered card');
  assert(!ui.text().includes('a'.repeat(64)),'Span hashes do not become user-facing prose');
}
module.exports={main};
if(require.main===module)main().then(()=>console.log('PASS: EvidenceSpan preservation, strict bounds, legacy compatibility and Unicode rendering.')).catch(error=>{console.error(error);process.exitCode=1;});
