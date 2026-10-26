from insight_agent.agent.prompts import UNTRUSTED, data_block, schema_hint
from insight_agent.llm import StructuredLLM
from insight_agent.schemas import ClaimKind, EventAnalysis, NewsEvent
from insight_agent.tools.profile import CHANNELS
from insight_agent.agent.evidence import pack_sources, quote_options

MAX_EVENT_CHARS = 6000


def analyze_event(llm: StructuredLLM, model: str, event: NewsEvent, *, source_texts: dict[str, str] | None = None) -> EventAnalysis:
    src = event.source.source_id
    system = (
        "Ты аналитик корпоративного банка. Разбери новость: выдели проверяемые факты (кто, что, когда, "
        "числа, причины), участников и каналы влияния на бизнес. Не делай выводов о конкретных компаниях. "
        f"{UNTRUSTED}\n"
        f"Каналы влияния выбирай из списка: {CHANNELS}.\n"
        "bank_queries - 1-3 коротких поисковых запроса к аналитике банка по теме события. "
        "open_questions - чего не хватает в новости для оценки последствий.\n"
        "У всех facts kind=fact; source_ids содержат только ID подтверждающих этот факт источников. "
        "Факт — дословная полная цитата, без перевода и пересказа. Выбирай полные quote_options, "
        "копируй evidence_span в evidence_spans; не меняй hash/offsets. Если полного фрагмента нет, пропусти факт. "
        "Не объединяй противоречащие сообщения в один подтверждённый факт.\n"
        f"Ответ - один JSON-объект по схеме: {schema_hint(EventAnalysis)}"
    )
    published = (event.published_at.isoformat() if event.published_at else
                 f"{event.published_date.isoformat()} (время неизвестно)" if event.published_date else "дата неизвестна")
    texts, _ = pack_sources({src: event.text} if source_texts is None else source_texts, MAX_EVENT_CHARS)
    blocks = "\n\n".join(f"source_id: {sid}\n{text}" for sid, text in texts.items())
    user = data_block(
        "news",
        f"event_id: {event.event_id}\nsource_id: {src}\nисточник: {event.source.title}\nдата: {published}\n"
        f"заголовок: {event.title}\nконфликты источников: {[c.model_dump() for c in event.conflicts]}\n\n{blocks}\n\n"
        f"quote_options: {quote_options({src: event.text} if source_texts is None else source_texts)}",
    )
    analysis = llm.generate(
        "analyze_event", model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
        EventAnalysis,
    )
    analysis.event_id = event.event_id
    analysis.impact_channels = [c for c in analysis.impact_channels if c in CHANNELS]
    for fact in analysis.facts:
        fact.kind = ClaimKind.fact
    return analysis
