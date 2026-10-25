from insight_agent.agent.prompts import UNTRUSTED, data_block, schema_hint
from insight_agent.llm import StructuredLLM
from insight_agent.schemas import ClaimKind, EventAnalysis, NewsEvent
from insight_agent.tools.profile import CHANNELS

MAX_EVENT_CHARS = 6000


def analyze_event(llm: StructuredLLM, model: str, event: NewsEvent) -> EventAnalysis:
    src = event.source.source_id
    system = (
        "Ты аналитик корпоративного банка. Разбери новость: выдели проверяемые факты (кто, что, когда, "
        "числа, причины), участников и каналы влияния на бизнес. Не делай выводов о конкретных компаниях. "
        f"{UNTRUSTED}\n"
        f"Каналы влияния выбирай из списка: {CHANNELS}.\n"
        "bank_queries - 1-3 коротких поисковых запроса к аналитике банка по теме события. "
        "open_questions - чего не хватает в новости для оценки последствий.\n"
        f"У всех facts kind=fact и source_ids=[\"{src}\"].\n"
        f"Ответ - один JSON-объект по схеме: {schema_hint(EventAnalysis)}"
    )
    published = event.published_at.isoformat() if event.published_at else "дата неизвестна"
    user = data_block(
        "news",
        f"event_id: {event.event_id}\nsource_id: {src}\nисточник: {event.source.title}\nдата: {published}\n"
        f"заголовок: {event.title}\n\n{event.text[:MAX_EVENT_CHARS]}",
    )
    analysis = llm.generate(
        "analyze_event", model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
        EventAnalysis,
    )
    analysis.event_id = event.event_id
    analysis.impact_channels = [c for c in analysis.impact_channels if c in CHANNELS]
    for fact in analysis.facts:
        fact.kind = ClaimKind.fact
        fact.source_ids = fact.source_ids or [src]
    return analysis
