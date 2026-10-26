package ru.businessinsights.backend.store;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.EventInput;
import ru.businessinsights.backend.api.ApiModels.EventVersionView;

@Repository
public class OperationStore {
    public record Operation(UUID id, String ownerId, String companyId, String role, String kind, UUID parentId,
                            String eventId, String status, JsonNode payload, JsonNode profileSnapshot, JsonNode result,
                            JsonNode trace, String traceState, List<String> warnings, String errorCode,
                            String errorMessage, boolean retryable, Instant createdAt, Instant updatedAt,
                            long eventVersion, boolean stale, Instant expiresAt, int relevanceScore,
                            List<String> relevanceReasons) {
        public Operation(UUID id, String ownerId, String companyId, String role, String kind, UUID parentId,
                         String eventId, String status, JsonNode payload, JsonNode profileSnapshot, JsonNode result,
                         JsonNode trace, String traceState, List<String> warnings, String errorCode,
                         String errorMessage, boolean retryable, Instant createdAt, Instant updatedAt) {
            this(id, ownerId, companyId, role, kind, parentId, eventId, status, payload, profileSnapshot, result,
                    trace, traceState, warnings, errorCode, errorMessage, retryable, createdAt, updatedAt,
                    1, false, createdAt == null ? null : createdAt.plus(java.time.Duration.ofDays(30)), 0, List.of());
        }
    }

    public record Interaction(boolean hidden, Instant openedAt,
                              ru.businessinsights.backend.api.ApiModels.FeedbackView feedback) {}

    public record Submission(Operation operation, boolean created) {
    }

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public OperationStore(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    @Transactional
    public EventInput registerEvent(EventInput event, String hash) {
        int inserted = jdbc.update("INSERT INTO backend_api.event(event_id,content_hash,payload,published_at) VALUES(?,?,?::jsonb,?) ON CONFLICT(event_id) DO NOTHING",
                event.eventId(), hash, json(event), event.publishedAt() == null ? null : java.sql.Timestamp.from(event.publishedAt()));
        if (inserted == 1) {
            saveRevision(event.eventId());
        }
        EventVersionView stored = currentEvent(event.eventId(), true);
        // Equality after decoding also accepts legacy JSON without the newly optional fields.
        if (!stored.event().equals(event)) {
            throw new ApiException(HttpStatus.CONFLICT, "EVENT_ID_CONFLICT", "Этот eventId уже связан с другим содержимым");
        }
        if (!stored.contentHash().equals(hash)) {
            JsonNode raw = jdbc.queryForObject("SELECT payload::text FROM backend_api.event WHERE event_id=?",
                    (rs, row) -> read(rs.getString(1), JsonNode.class), event.eventId());
            // Only a legacy representation missing newly optional fields may normalize to the same event.
            if (tree(event).equals(raw)) {
                throw new ApiException(HttpStatus.CONFLICT, "EVENT_ID_CONFLICT", "Хеш события отличается от сохранённого");
            }
        }
        return stored.event();
    }

    @Transactional
    public EventVersionView reviseEvent(EventInput event, String hash, long expectedVersion) {
        EventVersionView current = currentEvent(event.eventId(), true);
        requireManualEvent(event.eventId());
        if (current.event().equals(event)) {
            return current;
        }
        if (current.version() != expectedVersion) {
            throw new ApiException(HttpStatus.CONFLICT, "EVENT_VERSION_CONFLICT", "Версия события изменилась; перечитайте текущее событие");
        }
        return replaceEvent(event, hash);
    }

    @Transactional
    public EventVersionView synchronizeEvent(EventInput event, String hash) {
        int inserted = jdbc.update("INSERT INTO backend_api.event(event_id,content_hash,payload,published_at) VALUES(?,?,?::jsonb,?) ON CONFLICT(event_id) DO NOTHING",
                event.eventId(), hash, json(event), timestamp(event.publishedAt()));
        if (inserted == 1) {
            saveRevision(event.eventId());
        }
        EventVersionView current = currentEvent(event.eventId(), true);
        requireManualEvent(event.eventId());
        if (current.event().equals(event)) {
            return current;
        }
        // A replay of an already imported corpus revision must not roll the event back.
        boolean seen = !jdbc.query("SELECT 1 FROM backend_api.event_revision WHERE event_id=? AND content_hash=? LIMIT 1",
                (rs, n) -> 1, event.eventId(), hash).isEmpty();
        if (!seen) {
            seen = jdbc.query("SELECT payload::text FROM backend_api.event_revision WHERE event_id=?",
                    (rs, n) -> read(rs.getString(1), EventInput.class), event.eventId()).stream().anyMatch(event::equals);
        }
        return seen ? current : replaceEvent(event, hash);
    }

    private void requireManualEvent(String eventId) {
        if (Boolean.TRUE.equals(jdbc.queryForObject(
                "SELECT catalog_store_id IS NOT NULL FROM backend_api.event WHERE event_id=?", Boolean.class, eventId))) {
            throw new ApiException(HttpStatus.CONFLICT, "MATERIAL_EVENT_IMMUTABLE",
                    "Материал каталога исправляется через публикацию новой версии корпуса");
        }
    }

    private EventVersionView replaceEvent(EventInput event, String hash) {
        jdbc.update("UPDATE backend_api.event SET current_version=current_version+1,content_hash=?,payload=?::jsonb,published_at=?,received_at=now() WHERE event_id=?",
                hash, json(event), timestamp(event.publishedAt()), event.eventId());
        saveRevision(event.eventId());
        jdbc.update("UPDATE backend_api.operation SET stale=true,updated_at=now() WHERE event_id=? AND event_version < (SELECT current_version FROM backend_api.event WHERE event_id=?)",
                event.eventId(), event.eventId());
        return currentEvent(event.eventId());
    }

    private void saveRevision(String id) {
        jdbc.update("INSERT INTO backend_api.event_revision(event_id,version,content_hash,payload,published_at,received_at) SELECT event_id,current_version,content_hash,payload,published_at,received_at FROM backend_api.event WHERE event_id=?", id);
    }

    public EventVersionView currentEvent(String id) {
        return currentEvent(id, false);
    }

    public EventVersionView currentEventForShare(String id) {
        return jdbc.query("SELECT event_id,current_version,content_hash,received_at,payload::text FROM backend_api.event WHERE event_id=? FOR SHARE",
                        (rs, n) -> new EventVersionView(rs.getString(1), rs.getLong(2), rs.getString(3),
                                rs.getTimestamp(4).toInstant(), read(rs.getString(5), EventInput.class)), id)
                .stream().findFirst().orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "EVENT_NOT_FOUND", "Событие не найдено"));
    }

    private EventVersionView currentEvent(String id, boolean lock) {
        return jdbc.query("SELECT event_id,current_version,content_hash,received_at,payload::text FROM backend_api.event WHERE event_id=?" + (lock ? " FOR UPDATE" : ""),
                        (rs, n) -> new EventVersionView(rs.getString(1), rs.getLong(2), rs.getString(3), rs.getTimestamp(4).toInstant(), read(rs.getString(5), EventInput.class)), id)
                .stream().findFirst().orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "EVENT_NOT_FOUND", "Событие не найдено"));
    }

    public List<EventVersionView> eventHistory(String id, int limit, int offset) {
        currentEvent(id);
        return jdbc.query("SELECT event_id,version,content_hash,received_at,payload::text FROM backend_api.event_revision WHERE event_id=? ORDER BY version DESC LIMIT ? OFFSET ?",
                (rs, n) -> new EventVersionView(rs.getString(1), rs.getLong(2), rs.getString(3), rs.getTimestamp(4).toInstant(), read(rs.getString(5), EventInput.class)), id, limit, offset);
    }

    public EventInput event(String id) {
        return currentEvent(id).event();
    }

    public EventInput event(String id, long version) {
        return eventVersion(id, version).event();
    }

    public EventVersionView eventVersion(String id, long version) {
        return jdbc.query("SELECT event_id,version,content_hash,received_at,payload::text FROM backend_api.event_revision WHERE event_id=? AND version=?",
                        (rs, n) -> new EventVersionView(rs.getString(1), rs.getLong(2), rs.getString(3), rs.getTimestamp(4).toInstant(), read(rs.getString(5), EventInput.class)), id, version)
                .stream().findFirst().orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "EVENT_NOT_FOUND", "Событие не найдено"));
    }

    @Transactional
    public Submission create(String owner, String company, String role, String kind, UUID parent, String eventId,
                             String key, String hash, Object payload) {
        return createFrozen(owner, company, role, kind, parent, eventId, key, hash, payload, null);
    }

    @Transactional
    public Submission createFrozen(String owner, String company, String role, String kind, UUID parent, String eventId,
                                   String key, String hash, Object payload, Object profileSnapshot) {
        var replay = replay(owner, kind, key, hash);
        if (replay.isPresent()) return new Submission(replay.get(), false);
        var current = currentEventForShare(eventId);
        Instant published = current.event().publishedAt() == null ? current.receivedAt() : current.event().publishedAt();
        return createFrozen(owner, company, role, kind, parent, eventId, current.version(), 0, List.of(),
                published.plus(java.time.Duration.ofDays(30)), key, hash, payload, profileSnapshot);
    }

    @Transactional
    public Submission create(String owner, String company, String role, String kind, UUID parent, String eventId,
                             long eventVersion, int relevanceScore, List<String> relevanceReasons, Instant expiresAt,
                             String key, String hash, Object payload) {
        return createFrozen(owner, company, role, kind, parent, eventId, eventVersion, relevanceScore,
                relevanceReasons, expiresAt, key, hash, payload, null);
    }

    @Transactional
    public Submission createFrozen(String owner, String company, String role, String kind, UUID parent, String eventId,
                                   long eventVersion, int relevanceScore, List<String> relevanceReasons, Instant expiresAt,
                                   String key, String hash, Object payload, Object profileSnapshot) {
        var replay = replay(owner, kind, key, hash);
        if (replay.isPresent()) return new Submission(replay.get(), false);
        var current = currentEventForShare(eventId);
        UUID id = UUID.randomUUID();
        int inserted = jdbc.update("""
                INSERT INTO backend_api.operation(id,owner_id,company_id,role,kind,parent_id,event_id,event_version,stale,
                    relevance_score,relevance_reasons,expires_at,request_key,request_hash,status,payload,profile_snapshot)
                VALUES(?,?,?,?,?,?,?,?,?,?,?::jsonb,?,?,?,'QUEUED',?::jsonb,?::jsonb)
                ON CONFLICT(owner_id,kind,request_key) DO NOTHING
                """, id, owner, company, role, kind, parent, eventId, eventVersion, current.version() != eventVersion,
                relevanceScore, json(relevanceReasons == null ? List.of() : relevanceReasons), timestamp(expiresAt),
                key, hash, json(payload), profileSnapshot == null ? null : json(profileSnapshot));
        if (inserted == 1) return new Submission(find(id).orElseThrow(), true);
        return new Submission(replay(owner, kind, key, hash).orElseThrow(), false);
    }

    public Optional<Operation> replay(String owner, String kind, String key, String hash) {
        if (key == null) return Optional.empty();
        var rows = jdbc.query("SELECT * FROM backend_api.operation WHERE owner_id=? AND kind=? AND request_key=?", this::map, owner, kind, key);
        if (rows.isEmpty()) return Optional.empty();
        var existing = rows.getFirst();
        String previous = jdbc.queryForObject("SELECT request_hash FROM backend_api.operation WHERE id=?", String.class, existing.id());
        if (!hash.equals(previous)) throw new ApiException(HttpStatus.CONFLICT, "IDEMPOTENCY_CONFLICT", "Idempotency-Key уже использован для другого запроса");
        return Optional.of(existing);
    }

    public Optional<Operation> find(UUID id) {
        return jdbc.query("SELECT * FROM backend_api.operation WHERE id=?", this::map, id).stream().findFirst();
    }

    public Optional<Operation> owned(UUID id, String owner) {
        return jdbc.query("SELECT * FROM backend_api.operation WHERE id=? AND owner_id=?", this::map, id, owner).stream().findFirst();
    }

    public Optional<Operation> byKey(String owner, String kind, String key) {
        return jdbc.query("SELECT * FROM backend_api.operation WHERE owner_id=? AND kind=? AND request_key=?", this::map, owner, kind, key)
                .stream().findFirst();
    }

    public void selection(UUID id, int score, List<String> reasons, Instant expiresAt) {
        jdbc.update("UPDATE backend_api.operation SET relevance_score=?,relevance_reasons=?::jsonb,expires_at=?,updated_at=now() WHERE id=?",
                score, json(reasons), timestamp(expiresAt), id);
    }

    public boolean start(UUID id) {
        return jdbc.update("UPDATE backend_api.operation SET status='RUNNING',updated_at=now() WHERE id=? AND status='QUEUED'", id) == 1;
    }

    public void saveProfile(UUID id, Object profile) {
        jdbc.update("UPDATE backend_api.operation SET profile_snapshot=?::jsonb,updated_at=now() WHERE id=?", json(profile), id);
    }

    public void finish(UUID id, String status, Object result, List<String> warnings) {
        jdbc.update("UPDATE backend_api.operation SET status=?,result=?::jsonb,warnings=?::jsonb,updated_at=now() WHERE id=? AND status='RUNNING'",
                status, result == null ? null : json(result), json(warnings), id);
    }

    public void fail(UUID id, String code, String message, boolean retryable) {
        jdbc.update("UPDATE backend_api.operation SET status='FAILED',error_code=?,error_message=?,retryable=?,trace_state='UNAVAILABLE',updated_at=now() WHERE id=? AND status IN ('QUEUED','RUNNING')",
                code, message, retryable, id);
    }

    public void trace(UUID id, Object trace, String state, List<String> warnings) {
        jdbc.update("UPDATE backend_api.operation SET trace=?::jsonb,trace_state=?,warnings=?::jsonb,updated_at=now() WHERE id=?",
                trace == null ? null : json(trace), state, json(warnings), id);
    }

    public void interruptUnfinished() {
        jdbc.update("UPDATE backend_api.operation SET status='FAILED',error_code='BACKEND_RESTARTED',error_message='Операция прервана перезапуском; результат ML может существовать. Повтор только явным новым запросом.',retryable=false,trace_state='UNAVAILABLE',updated_at=now() WHERE status IN ('QUEUED','RUNNING')");
        jdbc.update("UPDATE backend_api.operation SET trace_state='UNAVAILABLE',updated_at=now() WHERE trace_state='PENDING' AND status NOT IN ('QUEUED','RUNNING')");
    }

    public List<Operation> feed(String owner, String company, String role, int limit, int offset, boolean includeHidden) {
        return jdbc.query("""
                SELECT o.* FROM backend_api.operation o JOIN backend_api.event e ON e.event_id=o.event_id
                LEFT JOIN backend_api.insight_state s ON s.operation_id=o.id AND s.owner_id=o.owner_id
                WHERE o.owner_id=? AND o.company_id=? AND o.role=? AND o.kind='ANALYSIS'
                  AND o.status IN ('SUCCEEDED','LIMITED') AND o.result IS NOT NULL AND (? OR NOT COALESCE(s.hidden,false))
                  AND NOT o.stale AND o.event_version=e.current_version AND o.expires_at>now()
                  AND NOT EXISTS (SELECT 1 FROM backend_api.operation newer WHERE newer.owner_id=o.owner_id
                    AND newer.company_id=o.company_id AND newer.role=o.role AND newer.kind='ANALYSIS'
                    AND newer.event_id=o.event_id AND newer.event_version=o.event_version
                    AND newer.status IN ('SUCCEEDED','LIMITED') AND newer.result IS NOT NULL
                    AND (newer.created_at,newer.id)>(o.created_at,o.id))
                ORDER BY o.relevance_score DESC, CASE o.result->>'impact_level' WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  o.created_at DESC,o.id DESC LIMIT ? OFFSET ?
                """, this::map, owner, company, role, includeHidden, limit, offset);
    }

    public Interaction interaction(UUID id, String owner) {
        return jdbc.query("""
                SELECT COALESCE(s.hidden,false) AS hidden,s.opened_at,f.rating,f.comment
                FROM backend_api.operation o
                LEFT JOIN backend_api.insight_state s ON s.operation_id=o.id AND s.owner_id=o.owner_id
                LEFT JOIN backend_api.feedback f ON f.operation_id=o.id AND f.owner_id=o.owner_id
                WHERE o.id=? AND o.owner_id=?
                """, (rs, row) -> new Interaction(rs.getBoolean("hidden"),
                    rs.getTimestamp("opened_at") == null ? null : rs.getTimestamp("opened_at").toInstant(),
                    rs.getString("rating") == null ? null : new ru.businessinsights.backend.api.ApiModels.FeedbackView(rs.getString("rating"), rs.getString("comment"))), id, owner)
                .stream().findFirst().orElse(new Interaction(false, null, null));
    }

    public boolean hidden(UUID id, String owner) {
        return jdbc.query("SELECT hidden FROM backend_api.insight_state WHERE operation_id=? AND owner_id=?", (rs, n) -> rs.getBoolean(1), id, owner)
                .stream().findFirst().orElse(false);
    }

    public void state(UUID id, String owner, boolean hidden) {
        jdbc.update("INSERT INTO backend_api.insight_state(operation_id,owner_id,hidden) VALUES(?,?,?) ON CONFLICT(operation_id,owner_id) DO UPDATE SET hidden=EXCLUDED.hidden,updated_at=now()", id, owner, hidden);
    }

    public void open(UUID id, String owner) {
        jdbc.update("INSERT INTO backend_api.insight_state(operation_id,owner_id,opened_at) VALUES(?,?,now()) ON CONFLICT(operation_id,owner_id) DO UPDATE SET opened_at=now(),updated_at=now()", id, owner);
    }

    public void feedback(UUID id, String owner, String rating, String comment) {
        jdbc.update("INSERT INTO backend_api.feedback(operation_id,owner_id,rating,comment) VALUES(?,?,?,?) ON CONFLICT(operation_id,owner_id) DO UPDATE SET rating=EXCLUDED.rating,comment=EXCLUDED.comment,updated_at=now()", id, owner, rating, comment);
    }

    public UUID action(UUID id, String owner, String action, String comment) {
        UUID actionId = UUID.randomUUID();
        jdbc.update("INSERT INTO backend_api.action_intent(id,operation_id,owner_id,action,comment) VALUES(?,?,?,?,?)", actionId, id, owner, action, comment);
        return actionId;
    }

    public JsonNode tree(Object value) {
        return mapper.valueToTree(value);
    }

    public <T> T convert(JsonNode node, Class<T> type) {
        return mapper.convertValue(node, type);
    }

    private String json(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Cannot serialize stored document", e);
        }
    }

    private <T> T read(String text, Class<T> type) {
        try {
            return mapper.readValue(text, type);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Cannot read stored document", e);
        }
    }

    private JsonNode node(ResultSet rs, String column) throws SQLException {
        String text = rs.getString(column);
        return text == null ? null : read(text, JsonNode.class);
    }

    private Operation map(ResultSet rs, int row) throws SQLException {
        JsonNode warnings = node(rs, "warnings");
        return new Operation(rs.getObject("id", UUID.class), rs.getString("owner_id"), rs.getString("company_id"), rs.getString("role"),
                rs.getString("kind"), rs.getObject("parent_id", UUID.class), rs.getString("event_id"), rs.getString("status"), node(rs, "payload"),
                node(rs, "profile_snapshot"), node(rs, "result"), node(rs, "trace"), rs.getString("trace_state"),
                mapper.convertValue(warnings, new TypeReference<List<String>>() {
                }), rs.getString("error_code"), rs.getString("error_message"),
                rs.getBoolean("retryable"), rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant(),
                rs.getLong("event_version"), rs.getBoolean("stale"), rs.getTimestamp("expires_at") == null ? null : rs.getTimestamp("expires_at").toInstant(),
                rs.getInt("relevance_score"), mapper.convertValue(node(rs, "relevance_reasons"), new TypeReference<List<String>>() {
        }));
    }

    private static java.sql.Timestamp timestamp(Instant value) {
        return value == null ? null : java.sql.Timestamp.from(value);
    }
}
