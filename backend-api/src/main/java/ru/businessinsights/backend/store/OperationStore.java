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

@Repository
public class OperationStore {
    public record Operation(UUID id, String ownerId, String companyId, String role, String kind, UUID parentId,
                            String eventId, String status, JsonNode payload, JsonNode profileSnapshot, JsonNode result,
                            JsonNode trace, String traceState, List<String> warnings, String errorCode,
                            String errorMessage, boolean retryable, Instant createdAt, Instant updatedAt) {
    }

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
        jdbc.update("INSERT INTO backend_api.event(event_id,content_hash,payload,published_at) VALUES(?,?,?::jsonb,?) ON CONFLICT(event_id) DO NOTHING",
                event.eventId(), hash, json(event), event.publishedAt() == null ? null : java.sql.Timestamp.from(event.publishedAt()));
        String storedHash = jdbc.queryForObject("SELECT content_hash FROM backend_api.event WHERE event_id=?", String.class, event.eventId());
        if (!hash.equals(storedHash)) {
            throw new ApiException(HttpStatus.CONFLICT, "EVENT_ID_CONFLICT", "Этот eventId уже связан с другим содержимым");
        }
        return event(event.eventId());
    }

    public EventInput event(String id) {
        return jdbc.query("SELECT payload::text FROM backend_api.event WHERE event_id=?", (rs, n) -> read(rs.getString(1), EventInput.class), id)
                .stream().findFirst().orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "EVENT_NOT_FOUND", "Событие не найдено"));
    }

    @Transactional
    public Submission create(String owner, String company, String role, String kind, UUID parent, String eventId,
                             String key, String hash, Object payload) {
        UUID id = UUID.randomUUID();
        int inserted = jdbc.update("""
                INSERT INTO backend_api.operation(id,owner_id,company_id,role,kind,parent_id,event_id,request_key,request_hash,status,payload)
                VALUES(?,?,?,?,?,?,?,?,?,'QUEUED',?::jsonb) ON CONFLICT(owner_id,kind,request_key) DO NOTHING
                """, id, owner, company, role, kind, parent, eventId, key, hash, json(payload));
        if (inserted == 1) {
            return new Submission(find(id).orElseThrow(), true);
        }
        var existing = jdbc.query("SELECT * FROM backend_api.operation WHERE owner_id=? AND kind=? AND request_key=?", this::map, owner, kind, key).getFirst();
        String existingHash = jdbc.queryForObject("SELECT request_hash FROM backend_api.operation WHERE id=?", String.class, existing.id());
        if (!hash.equals(existingHash)) {
            throw new ApiException(HttpStatus.CONFLICT, "IDEMPOTENCY_CONFLICT", "Idempotency-Key уже использован для другого запроса");
        }
        return new Submission(existing, false);
    }

    public Optional<Operation> find(UUID id) {
        return jdbc.query("SELECT * FROM backend_api.operation WHERE id=?", this::map, id).stream().findFirst();
    }

    public Optional<Operation> owned(UUID id, String owner) {
        return jdbc.query("SELECT * FROM backend_api.operation WHERE id=? AND owner_id=?", this::map, id, owner).stream().findFirst();
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
                SELECT o.* FROM backend_api.operation o LEFT JOIN backend_api.insight_state s ON s.operation_id=o.id AND s.owner_id=o.owner_id
                WHERE o.owner_id=? AND o.company_id=? AND o.role=? AND o.kind='ANALYSIS'
                  AND o.status IN ('SUCCEEDED','LIMITED') AND o.result IS NOT NULL AND (? OR NOT COALESCE(s.hidden,false))
                ORDER BY CASE o.result->>'impact_level' WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  o.created_at DESC,o.id DESC LIMIT ? OFFSET ?
                """, this::map, owner, company, role, includeHidden, limit, offset);
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
                rs.getBoolean("retryable"), rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant());
    }
}
