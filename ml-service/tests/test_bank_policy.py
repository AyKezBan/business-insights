from datetime import date

from insight_agent.schemas import BankFragment,NewsEvent,SourceRef
from insight_agent.tools.bank import BankIndex,BankSearchArgs


def fragment(identifier, day, **extra):
    return BankFragment(fragment_id=identifier,doc_id='document',doc_title='Прогноз ставки',
        text='Прогноз ключевой ставки',page=1,doc_date=day,
        review_status=extra.pop('review_status','verified'),**extra)


def test_date_quality_and_expired_horizon_cannot_be_bypassed_by_low_score():
    index=BankIndex([fragment('boundary',date(2026,4,27)),fragment('future',date(2026,10,25)),
        fragment('unknown',None),fragment('pending',date(2026,10,1),review_status='pending'),
        fragment('expired',date(2026,10,1),valid_until=date(2026,10,23))])
    hits=index.search(BankSearchArgs(query='ставка',not_before=date(2026,4,27),min_score=0),as_of=date(2026,10,24))
    assert [h.fragment.fragment_id for h in hits]==['boundary']


def test_superseded_forecast_is_replaced_only_when_revision_has_been_published():
    old=fragment('old',date(2026,8,5),statement_type='forecast',valid_until=date(2026,12,31))
    new=fragment('new',date(2026,9,11),statement_type='forecast',valid_until=date(2026,12,31),supersedes=['old'])
    later=fragment('later',date(2026,9,14))
    index=BankIndex([old,new,later])
    args=BankSearchArgs(query='ставки',min_score=0)
    assert [h.fragment.fragment_id for h in index.search(args,as_of=date(2026,9,10))]==['old']
    assert [h.fragment.fragment_id for h in index.search(args,as_of=date(2026,9,11))]==['new']


def test_same_score_sorts_by_stable_fragment_id():
    index=BankIndex([fragment('b',date(2026,9,1)),fragment('a',date(2026,9,1))])
    assert [h.fragment.fragment_id for h in index.search(BankSearchArgs(query='ставки',min_score=0),as_of=date(2026,10,24))]==['a','b']


def test_tool_policy_only_narrows_window_and_empty_intersection_returns_none():
    from insight_agent.materials.policy import bank_policy,restrict_bank_args
    event=NewsEvent(event_id='e',title='News',text='Text',source=SourceRef(source_id='n',kind='news',title='Source'))
    bounds=bank_policy(event,today=date(2026,10,24))
    assert bounds==(date(2026,4,27),date(2026,10,24))
    args=restrict_bank_args(BankSearchArgs(query='ставка',not_before=date(2020,1,1)),bounds)
    assert args.not_before==date(2026,4,27) and args.not_after==date(2026,10,24)
    assert restrict_bank_args(BankSearchArgs(query='ставка',not_after=date(2020,1,1)),bounds) is None


def test_registered_bank_tool_keeps_actual_asof_for_plan_and_question(request_,settings):
    from insight_agent.agent.orchestrator import InsightAgent
    from insight_agent.agent.state import AgentState
    from insight_agent.tools.news import NewsStore
    from insight_agent.trace import Tracer
    agent=InsightAgent(object(),settings,BankIndex([fragment('future',date(2030,1,1))]),NewsStore([]))
    state=AgentState(run_id='policy',request=request_,signals=[],missing_profile=[])
    tools=agent.tools_for(state,Tracer('policy',settings.trace_dir))
    for step in ('execute_plan','question_search','adapt'):
        assert tools.call('search_bank_analytics',{'query':'ставки','min_score':0,
            'not_before':'2000-01-01','not_after':'2040-01-01'},step=step)==[]
