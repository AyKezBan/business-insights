package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;
import ru.businessinsights.backend.data.MaterialCatalogRepository.CatalogToken;
import ru.businessinsights.backend.data.MaterialCatalogRepository.MaterialEvent;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.nio.charset.StandardCharsets;

/** Read-only client for the frozen catalog envelope; transport limits are shared with ML calls. */
@Component
public class MaterialCatalogClient {
    public record CatalogPage(CatalogToken token, List<MaterialEvent> events, String nextCursor) {
        public CatalogPage { events = List.copyOf(events); }
    }
    private final HttpMlClient transport;
    private final ObjectMapper json;
    private final PinnedSchemaValidator schemas;
    public MaterialCatalogClient(HttpMlClient transport, ObjectMapper json) {
        this.transport = transport;
        this.json = json.copy();
        this.schemas = new PinnedSchemaValidator(this.json);
    }
    public CatalogPage page(String revision, String cursor, int limit) {
        validateRevision(revision);
        if (limit < 1 || limit > 100 || (cursor != null && !boundedOpaque(cursor, 4096)))
            throw rejected("Invalid catalog page limit or cursor");
        var result = read("/v1/materials/events?" + (revision == null ? "" : "revision=" + HttpMlClient.segment(revision) + "&")
                + (cursor == null ? "" : "cursor=" + HttpMlClient.segment(cursor) + "&") + "limit=" + limit, revision);
        if (result.events().size() > limit || (cursor != null && cursor.equals(result.nextCursor())))
            throw invalid("Catalog pagination exceeds its bounds or does not advance");
        return result;
    }
    public CatalogPage event(String eventId, String revision) {
        validateRevision(revision);
        if (!boundedOpaque(eventId, 200)) throw rejected("Invalid material event ID");
        String segment = HttpMlClient.segment(eventId);
        if (eventId.equals(".") || eventId.equals("..")) segment = segment.replace(".", "%2E");
        var result = read("/v1/materials/events/" + segment
                + (revision == null ? "" : "?revision=" + HttpMlClient.segment(revision)), revision);
        if (result.events().size() != 1 || !eventId.equals(result.events().getFirst().event().eventId()) || result.nextCursor() != null)
            throw invalid("Single-event catalog response has a different identity or pagination");
        return result;
    }

    private CatalogPage read(String path, String revision) {
        JsonNode root = transport.getJson(path);
        try {
            exactObject(root, Set.of("catalog_token", "events", "next_cursor"));
            JsonNode t = root.get("catalog_token");
            exactObject(t, Set.of("store_id", "generation", "revision"));
            if (!t.get("store_id").isTextual() || t.get("store_id").textValue().isBlank()
                    || !t.get("generation").isIntegralNumber() || !t.get("generation").canConvertToLong()
                    || !sha(t.get("revision"))) throw invalid("Invalid catalog publication token");
            var token = new CatalogToken(t.get("store_id").textValue(), t.get("generation").longValue(), t.get("revision").textValue());
            if (revision != null && !revision.equals(token.revision())) throw invalid("Catalog revision differs from the requested revision");
            JsonNode items = root.get("events"), continuation = root.get("next_cursor");
            if (!items.isArray() || items.size() > 100 || !(continuation.isNull()
                    || (continuation.isTextual() && boundedOpaque(continuation.textValue(), 4096))))
                throw invalid("Invalid catalog events or cursor");
            String next = continuation.isNull() ? null : continuation.textValue();
            if (items.isEmpty() && next != null) throw invalid("Empty catalog page cannot continue");
            var events = new ArrayList<MaterialEvent>();
            Set<String> ids = new HashSet<>();
            for (JsonNode item : items) {
                exactObject(item, Set.of("event", "event_content_sha256"));
                if (!sha(item.get("event_content_sha256"))) throw invalid("Invalid semantic event hash");
                schemas.validateDefinition("insight-request", "NewsEvent", item.get("event"));
                var event = json.treeToValue(item.get("event"), MlContracts.NewsEvent.class);
                validateSources(event);
                if (event.eventId().isBlank() || !ids.add(event.eventId())) throw invalid("Catalog event IDs must be nonempty and unique");
                events.add(new MaterialEvent(event, item.get("event_content_sha256").textValue()));
            }
            return new CatalogPage(token, events, next);
        } catch (MlException failure) {
            throw failure;
        } catch (com.fasterxml.jackson.core.JsonProcessingException | IllegalArgumentException failure) {
            throw new MlException(MlException.Code.INVALID_RESPONSE, "Invalid material catalog response", false, failure);
        }
    }

    private static void validateSources(MlContracts.NewsEvent event) {
        Set<String> ids = new HashSet<>();
        List<MlContracts.SourceRef> sources = new ArrayList<>();
        sources.add(event.source()); sources.addAll(event.relatedSources());
        for (var source : sources) {
            if (source.kind() != MlContracts.SourceKind.news || source.sourceId().isBlank() || !ids.add(source.sourceId()))
                throw invalid("Catalog events require distinct external news source IDs");
        }
        for (var conflict : event.conflicts()) for (String id : conflict.sourceIds())
            if (!ids.contains(id)) throw invalid("Catalog conflict references an absent source");
    }

    private static void exactObject(JsonNode value, Set<String> fields) {
        if (value == null || !value.isObject() || value.size() != fields.size()
                || fields.stream().anyMatch(field -> !value.has(field))) throw invalid("Invalid catalog envelope shape");
    }

    private static boolean sha(JsonNode value) {
        return value != null && value.isTextual() && value.textValue().matches("[a-f0-9]{64}");
    }

    private static void validateRevision(String revision) {
        if (revision != null && !revision.matches("[a-f0-9]{64}")) throw rejected("Invalid catalog revision");
    }

    private static boolean boundedOpaque(String value, int maxBytes) {
        if (value == null || value.isBlank() || value.length() > maxBytes
                || value.getBytes(StandardCharsets.UTF_8).length > maxBytes) return false;
        return value.codePoints().noneMatch(code -> Character.isISOControl(code) || code >= 0xD800 && code <= 0xDFFF);
    }

    private static MlException rejected(String message) { return new MlException(MlException.Code.REQUEST_REJECTED, message, false); }
    private static MlException invalid(String message) { return new MlException(MlException.Code.INVALID_RESPONSE, message, false); }
}
