"""Synthetic offline integration; real-PDF acceptance is reported separately."""
import json
from datetime import datetime,date,timezone

from fastapi.testclient import TestClient

from fakes import ScriptedClient,analysis_reply,draft_reply, reviewed_semantics, semantic_contents
from insight_agent.api import build_agent,create_app
from insight_agent.materials.bank_corpus import prepare_bank
from insight_agent.materials.dedup import merge_news
from insight_agent.materials.models import RawNews
from insight_agent.materials.store import publish_snapshot
from insight_agent.trace import load_trace
from test_pdf_extraction import make_pdf,FakeOCR


def test_pdf_to_insight_original_url_and_trace_hash(tmp_path,settings,request_):
    inputs=tmp_path/'input';inputs.mkdir()
    meta=make_pdf(inputs/'x.pdf','Bank forecast: rate 18% until end 2026.')
    meta.doc_date=date(2026,8,1)
    meta.topics=['cost_of_debt']
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'documents':[meta.model_dump(mode='json')]}))
    root=tmp_path/'materials'
    snapshot=prepare_bank(inputs,manifest,root,FakeOCR())
    f=snapshot.bank_fragments[0];f.review_status='verified';f.statement_type='forecast';f.valid_until=date(2026,12,31)
    raw=RawNews(url='https://www.cbr.ru/press/integration',title=request_.event.title,text=request_.event.text,
        published_at=request_.event.published_at,retrieved_at=datetime(2026,8,1,tzinfo=timezone.utc))
    merged=merge_news([],[],[raw],observed_at=raw.retrieved_at)
    snapshot.news,snapshot.source_versions=merged.events,merged.versions
    publish_snapshot(root,snapshot)
    event=snapshot.news[0]
    analysis=analysis_reply();analysis['event_id']=event.event_id
    for c in analysis['facts']: c['source_ids']=[event.source.source_id]
    draft=draft_reply();draft['scenarios']=[]
    draft['bank_view'][0]['text']=f.text
    for key in ('why_it_matters','consequences','risks','actions','bank_view'):
        for c in draft[key]:
            c['source_ids']=[f.fragment_id if s=='bank_demo_rate_p3' else event.source.source_id for s in c.get('source_ids',[])]
    plan={'steps':[{'tool':'search_bank_analytics','args':{'query':'Bank forecast rate','min_score':0.3},'purpose':'evidence'}]}
    client=ScriptedClient({'analyze_event':[analysis],'plan':[plan],'personalize':[draft],
        'semantic_verify':[reviewed_semantics(semantic_contents(draft))]})
    settings.materials_dir=root;settings.materials_public_base_url='http://testserver'
    agent=build_agent(settings,client)
    http=TestClient(create_app(agent))
    request=request_.model_copy(update={'event':event})
    response=http.post('/v1/insights',json=request.model_dump(mode='json'))
    assert response.status_code==200
    body=response.json()
    ref=next(s for s in body['sources'] if s['kind']=='bank')
    assert ref['page']==1 and ref['content_sha256']==f.content_sha256
    pdf=http.get(ref['url'].split('#')[0])
    assert pdf.status_code==200 and pdf.content==(inputs/'x.pdf').read_bytes()
    trace=load_trace(settings.trace_dir,body['run_id'])
    result=next(e for e in trace if e.type=='tool_call' and e.data.get('tool')=='search_bank_analytics')
    assert f.content_sha256 in json.dumps(result.model_dump(mode='json'))


def test_bank_search_does_not_treat_generic_risk_or_year_as_topic_evidence():
    from insight_agent.schemas import BankFragment
    from insight_agent.tools.bank import BankIndex,BankSearchArgs
    fragment=BankFragment(fragment_id='synthetic',doc_id='synthetic',doc_title='Макростатистика 2026',
        doc_date=date(2026,9,7),page=1,review_status='verified',statement_type='bank_assessment',
        text='Июльская статистика говорит о сохранении проинфляционных рисков в экономике.')
    index=BankIndex([fragment])
    for query in ['Риск влияния манипуляций на котировки акций Сбербанка и Совкомбанка',
                  'Анализ контрагентов ООО КИНЕТИК КАПИТАЛ по сделкам в I квартале 2026 года',
                  'Статистика нарушений закона в брокерском секторе']:
        assert index.search(BankSearchArgs(query=query,min_score=0.1),as_of=date(2026,10,24))==[]
    assert index.search(BankSearchArgs(query='Проинфляционные риски в экономике',min_score=0.1),as_of=date(2026,10,24))
