"""Fixed, reviewed public sources; never import adapters or open URLs by user input."""
from insight_agent.materials.sources import NewsSourceAdapter, SourceSpec
from insight_agent.materials.transport import ControlledTransport

_SPECS = {
    'cbr_press': SourceSpec(source_id='cbr_press', publisher_id='cbr',
        hosts=('cbr.ru', 'www.cbr.ru'), allowed_paths=('/rss/RssPress', '/press/'),
        discovery_url='https://www.cbr.ru/rss/RssPress', parser_version='cbr-press-v1',
        language='ru', material_type='news_article', date_policy='explicit_offset',
        rights_url='https://www.cbr.ru/about/'),
    'fns_news': SourceSpec(source_id='fns_news', publisher_id='fns',
        hosts=('www.nalog.gov.ru', 'nalog.gov.ru'), allowed_paths=('/rn77/news/activities_fts/',),
        discovery_url='https://www.nalog.gov.ru/rn77/news/activities_fts/', parser_version='fns-news-v1',
        language='ru', material_type='news_article', date_policy='day_only',
        rights_url='https://www.nalog.gov.ru/rn77/copyright/'),
    'eia_today': SourceSpec(source_id='eia_today', publisher_id='eia',
        hosts=('www.eia.gov', 'eia.gov'),
        allowed_paths=('/todayinenergy/detail.php',),
        discovery_url='https://www.eia.gov/todayinenergy/', parser_version='eia-today-v1',
        language='en', material_type='public_analysis', date_policy='day_only',
        rights_url='https://www.eia.gov/about/copyrights_reuse.php'),
}


def get_spec(source_id: str) -> SourceSpec:
    try:
        return _SPECS[source_id]
    except KeyError:
        raise ValueError(f'Unknown source ID: {source_id}') from None


def get_source(source_id: str, transport: ControlledTransport) -> NewsSourceAdapter:
    spec = get_spec(source_id)
    if source_id == 'cbr_press':
        from insight_agent.materials.adapters.cbr import CbrNewsSource
        return CbrNewsSource(transport, spec)
    if source_id == 'fns_news':
        from insight_agent.materials.adapters.fns import FnsNewsSource
        return FnsNewsSource(transport, spec)
    from insight_agent.materials.adapters.eia import EiaTodaySource
    return EiaTodaySource(transport, spec)
