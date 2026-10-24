from datetime import datetime, timedelta, timezone

import pytest

from insight_agent.materials.models import RawNews

T=datetime(2026,10,24,10,tzinfo=timezone.utc)


def raw(url, text, seen=T):
    return RawNews(url=url,title='Rate decision',text=text,published_at=T,retrieved_at=seen)


def test_repeat_and_two_publishers_keep_one_event_with_exact_source_texts():
    from insight_agent.materials.dedup import merge_news
    a=raw('https://www.cbr.ru/a?utm_source=x','Rate remains 17%.')
    b=raw('https://www.cbr.ru/b','Rate remains 17%.')
    first=merge_news([],[],[a,b],observed_at=T)
    second=merge_news(first.events,first.versions,[a,b],observed_at=T+timedelta(hours=1))
    assert len(first.events)==1 and len(first.versions)==2
    assert first.events==second.events and first.versions==second.versions
    assert len(first.events[0].related_sources)==1


def test_same_url_updates_without_changing_event_id_and_does_not_lose_history():
    from insight_agent.materials.dedup import merge_news
    a=raw('https://www.cbr.ru/a','Rate remains 17%.')
    first=merge_news([],[],[a],observed_at=T)
    second=merge_news(first.events,first.versions,[raw(a.url,'Rate remains 18%.',T+timedelta(hours=1))],observed_at=T+timedelta(hours=1))
    assert second.events[0].event_id==first.events[0].event_id
    assert second.events[0].text=='Rate remains 18%.'
    assert {v.text for v in second.versions}=={'Rate remains 17%.','Rate remains 18%.'}


@pytest.mark.parametrize('other',['Rate remains 18%.','Rate does not remain 17%.'])
def test_different_number_or_negation_is_not_an_exact_duplicate(other):
    from insight_agent.materials.dedup import merge_news
    result=merge_news([],[],[raw('https://www.cbr.ru/a','Rate remains 17%.'),raw('https://www.cbr.ru/b',other)],observed_at=T)
    assert len(result.events)==2


def test_diverged_duplicate_cluster_is_not_rolled_back_by_unchanged_peer():
    from insight_agent.materials.dedup import merge_news
    a=raw('https://www.cbr.ru/a','Rate remains 17%.');b=raw('https://www.cbr.ru/b',a.text)
    first=merge_news([],[],[a,b],observed_at=T)
    later=T+timedelta(hours=1)
    second=merge_news(first.events,first.versions,[raw(a.url,'Rate remains 18%.',later)],observed_at=later)
    third=merge_news(second.events,second.versions,[raw(b.url,b.text,later)],observed_at=later)
    assert third.events[0].event_id==first.events[0].event_id
    assert third.events[0].text=='Rate remains 18%.'
    assert third.events[0].conflicts and not third.events[0].conflicts[0].resolved
    assert len(third.versions)==3


def test_batch_order_and_real_reversion_are_deterministic():
    from insight_agent.materials.dedup import merge_news
    inputs=[raw('https://www.cbr.ru/b','Rate 17%'),raw('https://www.cbr.ru/a','Rate 17%')]
    first=merge_news([],[],inputs,observed_at=T)
    assert first==merge_news([],[],list(reversed(inputs)),observed_at=T)
    for i,text in [(1,'Rate 18%'),(2,'Rate 17%')]:
        at=T+timedelta(hours=i)
        first=merge_news(first.events,first.versions,[raw('https://www.cbr.ru/a',text,at)],observed_at=at)
    revisions=[v.revision for v in first.versions if v.url.endswith('/a')]
    assert revisions==[1,2,3]
    assert len({v.source_id for v in first.versions})==4


def test_url_identity_preserves_meaningful_parameters():
    from insight_agent.materials.dedup import canonicalize_url
    assert canonicalize_url('https://www.cbr.ru/press/?file=one.htm&utm_campaign=x#top')=='https://www.cbr.ru/press/?file=one.htm'
    assert canonicalize_url('https://www.cbr.ru/press/?file=two.htm')!='https://www.cbr.ru/press/?file=one.htm'


def test_unordered_metadata_versions_are_rejected():
    from insight_agent.materials.dedup import merge_news
    a=raw('https://www.cbr.ru/a','Rate 17%')
    b=a.model_copy(update={'title':'Different title'})
    with pytest.raises(ValueError,match='unordered'):
        merge_news([],[],[a,b],observed_at=T)


def test_manual_link_preserves_both_primary_texts_and_marks_disagreement():
    from insight_agent.materials.dedup import merge_news,link_source
    first=merge_news([],[],[raw('https://www.cbr.ru/a','Rate 17%'),raw('https://www.cbr.ru/b','Rate 18%')],observed_at=T)
    target=first.events[0].event_id
    other=first.events[1].source.source_id
    joined=link_source(first.events,first.versions,target,other)
    assert len(joined.events)==1
    assert len(joined.events[0].related_sources)==1
    assert joined.events[0].conflicts
    assert {v.text for v in joined.versions}=={'Rate 17%','Rate 18%'}
