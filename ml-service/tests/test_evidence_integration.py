from datetime import datetime,timezone
import hashlib

import pytest
from pydantic import ValidationError

from fakes import ScriptedClient,analysis_reply,draft_reply,plan_reply,FIXTURES
from insight_agent.schemas import NewsEvent,SourceRef,EventAnalysis
from insight_agent.tools.bank import BankIndex


def script(analysis=None,draft=None):
    return ScriptedClient({'analyze_event':[analysis or analysis_reply()], 'plan':[plan_reply()],
        'personalize':[draft or draft_reply()], 'revise':[draft or draft_reply()]})


def test_bank_source_keeps_document_page_url_hash_in_final_insight(make_agent,request_):
    bank=BankIndex.from_jsonl(FIXTURES/'bank_fragments.jsonl')
    f=bank.fragments[0]
    f.source_url='/v1/materials/documents/demo_macro/file#page=3'
    f.content_sha256=hashlib.sha256(f.text.encode()).hexdigest()
    insight=make_agent(script(),bank=bank).run(request_)
    ref=next(s for s in insight.sources if s.source_id==f.fragment_id)
    assert (ref.doc_id,ref.page,ref.url,ref.content_sha256)==('demo_macro',3,f.source_url,f.content_sha256)
    assert ref.excerpt in f.text


def test_bad_event_fact_cannot_survive_through_summary_or_public_free_text(make_agent,request_):
    analysis=analysis_reply()
    analysis['facts'].append({'text':'Банк ожидает 999%.','kind':'fact','source_ids':['news_demo_rate']})
    analysis['summary']='Банк ожидает 999%.'
    draft=draft_reply();draft.update(headline='Банк ожидает 999%.',impact_rationale='Банк ожидает 999%.',model_vs_bank=['Банк ожидает 999%.'])
    insight=make_agent(script(analysis,draft)).run(request_)
    assert insight.status=='limited'
    assert all('999%' not in c.text for c in insight.what_happened)
    assert '999%' not in insight.headline+insight.impact_rationale+' '.join(insight.model_vs_bank)


def test_updated_event_text_invalidates_analysis_cache(make_agent,request_):
    client=script()
    agent=make_agent(client)
    agent.run(request_)
    event=request_.event.model_copy(update={'text':request_.event.text+' New confirmed condition.'})
    agent.run(request_.model_copy(update={'event':event}))
    assert client.steps().count('analyze_event')==2


def test_qa_does_not_return_rejected_bank_claim_in_free_text(make_agent,request_):
    from insight_agent.agent.qa import answer_question
    client=script();agent=make_agent(client)
    insight=agent.run(request_)
    client.script['answer']=[{'answerable':True,'text':'Банк ожидает 999%.',
        'claims':[{'text':'Банк ожидает 999%.','kind':'assessment','source_ids':['news_demo_rate']}]}]
    answer=answer_question(agent,insight.insight_id,'Какой прогноз банка?')
    assert answer.status=='no_answer' and answer.claims==[]
    assert '999%' not in answer.text


def test_fact_cannot_borrow_primary_number_when_it_cites_only_second_source(request_):
    from insight_agent.agent.critic import check_event_analysis
    ref=SourceRef(source_id='second',kind='news',title='Second')
    event=request_.event.model_copy(update={'related_sources':[ref]})
    analysis=EventAnalysis(event_id=event.event_id,summary='Unsupported',facts=[
        {'text':'Rate is 18%.','kind':'fact','source_ids':['second']}])
    issues=check_event_analysis(event,analysis,{'news_demo_rate':'Rate is 18%.','second':'Rate is 25%.'})
    assert any(i.code=='unsupported_number' for i in issues)


def test_resolved_secondary_source_is_visible_to_analyzer_and_retained(make_agent,request_):
    from insight_agent.materials.models import SourceVersion
    from insight_agent.tools.news import NewsStore
    now=datetime(2026,10,24,tzinfo=timezone.utc)
    text='A separate source reports 25%.'
    h=hashlib.sha256(text.encode()).hexdigest()
    version=SourceVersion(source_id='second',event_id=request_.event.event_id,url='https://www.cbr.ru/second',
        revision=1,content_sha256=h,published_at=now,first_seen_at=now,retrieved_at=now,text=text,title='Second')
    analysis=analysis_reply();analysis['facts'].append({'text':'Separate source reports 25%.','kind':'fact','source_ids':['second']})
    client=script(analysis);agent=make_agent(client)
    agent.news=NewsStore([],source_versions=[version])
    event=request_.event.model_copy(update={'related_sources':[SourceRef(source_id='second',kind='news',title='Second',content_sha256=h)]})
    result=agent.run(request_.model_copy(update={'event':event}))
    assert any(c.source_ids==['second'] and '25%' in c.text for c in result.what_happened)
    assert 'second' in {s.source_id for s in result.sources}


def test_metadata_without_source_text_does_not_ground_qualitative_fact(make_agent,request_):
    analysis=analysis_reply();analysis['facts'].append({'text':'An unsupported qualitative fact.','kind':'fact','source_ids':['second']})
    event=request_.event.model_copy(update={'related_sources':[SourceRef(source_id='second',kind='news',title='Missing text')]})
    result=make_agent(script(analysis)).run(request_.model_copy(update={'event':event}))
    assert all(c.source_ids!=['second'] for c in result.what_happened)
    assert result.status=='limited'


def test_scenario_with_unknown_source_is_removed(make_agent,request_):
    draft=draft_reply();draft['scenarios'][0]['source_ids']=['nonexistent']
    insight=make_agent(script(draft=draft)).run(request_)
    assert insight.scenarios==[] and insight.status=='limited'


def test_external_news_cannot_claim_bank_source_kind():
    with pytest.raises(ValidationError):
        NewsEvent(event_id='x',title='Not bank research',text='External source',
            source=SourceRef(source_id='x',kind='bank',title='External'))


def test_long_source_is_limited_without_destroying_original_text(make_agent,request_):
    event=request_.event.model_copy(update={'text':request_.event.text+'\n\n'+('Additional source paragraph. '*500)})
    agent=make_agent(script())
    result=agent.run(request_.model_copy(update={'event':event}))
    assert result.status=='limited'
    assert agent.records[result.insight_id].state.event.text==event.text
    assert any('контекст' in reason.lower() for reason in result.missing_data)


def test_percentage_cannot_borrow_number_from_quarter_label(request_):
    from insight_agent.agent.critic import check_claims
    from insight_agent.schemas import Claim
    from test_critic import _state
    state=_state(request_)
    state.bank['bank_demo_rate_p3'].text='Q2 2027 forecast: the rate remains 18%.'
    claim=Claim(text='The bank forecasts 2%.',kind='assessment',source_ids=['bank_demo_rate_p3'])
    assert any(i.code=='unsupported_quantity' for i in check_claims([claim],state,section='bank_view'))


def test_conflicting_sources_remain_visible_and_limit_insight(make_agent,request_):
    from insight_agent.schemas import SourceConflict
    event=request_.event.model_copy(update={
        'related_sources':[SourceRef(source_id='other',kind='news',title='Conflicting publisher',url='https://example.org/other')],
        'conflicts':[SourceConflict(reason='different_versions',source_ids=[request_.event.source.source_id,'other'])]})
    result=make_agent(script()).run(request_.model_copy(update={'event':event}))
    assert result.status=='limited'
    assert {'other',request_.event.source.source_id} <= {s.source_id for s in result.sources}
    assert any('расходятся' in s for s in result.missing_data)


def test_wrong_hash_of_primary_version_is_not_usable_evidence(make_agent,request_):
    ref=request_.event.source.model_copy(update={'content_sha256':'0'*64})
    event=request_.event.model_copy(update={'source':ref})
    result=make_agent(script()).run(request_.model_copy(update={'event':event}))
    assert result.what_happened==[] and result.status=='limited'
    assert any(i.code=='unresolved_source' for i in result.self_check.issues)


def test_reported_statistic_is_not_bank_assessment(request_):
    from insight_agent.agent.critic import check_claims
    from insight_agent.schemas import Claim
    from test_critic import _state
    state=_state(request_)
    state.bank['bank_demo_rate_p3'].statement_type='reported_fact'
    claim=Claim(text='The bank forecasts 18%.',kind='assessment',source_ids=['bank_demo_rate_p3'])
    assert any(i.code=='unverified_bank_assessment' for i in check_claims([claim],state,section='bank_view'))


def test_resolved_conflicts_from_retrieved_news_survive_card_and_qa(make_agent,request_):
    from insight_agent.materials.models import SourceVersion
    from insight_agent.schemas import SourceConflict
    from insight_agent.tools.news import NewsStore
    from insight_agent.agent.qa import answer_question
    at=datetime(2026,10,23,tzinfo=timezone.utc)
    texts={'related_a':'Ставки по корпоративным кредитам: показатель 17%.',
           'related_b':'Ставки по корпоративным кредитам: показатель 18%.'}
    versions=[SourceVersion(source_id=sid,event_id='related_event',url='https://www.cbr.ru/'+sid,revision=1,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),published_at=at,first_seen_at=at,
        retrieved_at=at,text=text,title='Ставки по корпоративным кредитам') for sid,text in texts.items()]
    refs=[SourceRef(source_id=v.source_id,kind='news',title=v.title,url=v.url,content_sha256=v.content_sha256) for v in versions]
    found=NewsEvent(event_id='related_event',title='Ставки по корпоративным кредитам',text=texts['related_a'],
        published_at=at,source=refs[0],related_sources=[refs[1]],
        conflicts=[SourceConflict(reason='different_numbers',source_ids=list(texts))])
    draft=draft_reply();draft['why_it_matters'].append({'text':'Показатель 17%.','kind':'fact','source_ids':['related_a']})
    seen=[]
    def capture(messages):
        seen.append(messages[-1]['content']);return draft
    client=script(draft=draft);client.script['personalize']=[capture]
    agent=make_agent(client);agent.news=NewsStore([found],versions)
    insight=agent.run(request_)
    assert insight.status=='limited'
    assert {'related_a','related_b'} <= {s.source_id for s in insight.sources}
    assert any('расходятся' in m for m in insight.missing_data)
    assert 'different_numbers' in seen[0] and 'related_b' in seen[0]
    client.script['answer']=[{'answerable':True,'text':'17%.','claims':[
        {'text':'Показатель 17%.','kind':'fact','source_ids':['related_a']}]}]
    answer=answer_question(agent,insight.insight_id,'Ставки по корпоративным кредитам?')
    assert {'related_a','related_b'} <= {s.source_id for s in answer.sources}
    assert 'расходятся' in answer.text
