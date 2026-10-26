package ru.businessinsights.backend.data;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.EventInput;
import ru.businessinsights.backend.api.ApiModels.SourceInput;
import ru.businessinsights.backend.ml.MlContracts.NewsEvent;
import ru.businessinsights.backend.ml.MlContracts.SourceRef;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;
import java.util.Objects;

/** Only trusted server-to-server catalog readers may publish into this repository. */
@Repository
public class MaterialCatalogRepository {
    public record CatalogToken(String storeId, long generation, String revision) {
        public CatalogToken {
            if (storeId == null || storeId.isBlank() || generation < 0 || revision == null || !revision.matches("[a-f0-9]{64}"))
                throw new IllegalArgumentException("Invalid catalog token");
        }
    }
    public record MaterialEvent(NewsEvent event, String eventContentSha256) {
        public MaterialEvent {
            if (event == null || event.eventId() == null || event.eventId().isBlank() || eventContentSha256 == null || !eventContentSha256.matches("[a-f0-9]{64}"))
                throw new IllegalArgumentException("Invalid material event");
        }
    }
    public record CurrentEvent(NewsEvent event, String eventContentSha256, CatalogToken token) {}
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    public MaterialCatalogRepository(JdbcTemplate jdbc, ObjectMapper json) { this.jdbc = jdbc; this.json = json; }

    public CatalogToken head() {
        return jdbc.queryForObject("SELECT * FROM backend_api.material_catalog_head WHERE singleton", (rs, row) -> token(rs));
    }

    /** CAS is against the complete publication token, independent of semantic event hashes. */
    @Transactional
    public CatalogToken synchronize(CatalogToken expected, CatalogToken incoming, List<MaterialEvent> events) {
        CatalogToken current = jdbc.queryForObject("SELECT * FROM backend_api.material_catalog_head WHERE singleton FOR UPDATE", (rs, row) -> token(rs));
        if (incoming.generation() < 1) throw conflict("CATALOG_UNINITIALIZED", "Каталог ещё не опубликован writer-ом");
        boolean replay = incoming.equals(current);
        if (!replay) {
            if (!Objects.equals(expected, current)) throw conflict("CATALOG_CAS_CONFLICT", "Публикация каталога уже изменилась");
            if (current != null && !current.storeId().equals(incoming.storeId())) throw conflict("CATALOG_RESEED_REQUIRED", "Смена хранилища требует явной инициализации");
            if (current != null && incoming.generation() <= current.generation()) throw conflict("CATALOG_STALE", "Запоздалая публикация каталога отклонена");
        }
        for (MaterialEvent material : List.copyOf(events)) {
            NewsEvent event = material.event();
            var rows = jdbc.queryForList("SELECT catalog_store_id,corpus_revision,event_content_sha256 FROM backend_api.event WHERE event_id=? FOR UPDATE", event.eventId());
            if (!rows.isEmpty()) {
                var old = rows.getFirst();
                if (!incoming.storeId().equals(old.get("catalog_store_id"))) throw conflict("EVENT_ID_CONFLICT", "ID занят событием другого происхождения");
                if (incoming.revision().equals(old.get("corpus_revision")) && !material.eventContentSha256().equals(old.get("event_content_sha256")))
                    throw conflict("CATALOG_CONTENT_CONFLICT", "Одна revision содержит разные версии события");
            }
            EventInput publicEvent = new EventInput(event.eventId(), event.title(), event.text(), event.publishedAt(), source(event.source()),
                    event.relatedSources().stream().map(MaterialCatalogRepository::source).toList(), event.topics(),
                    event.conflicts().stream().map(c -> new ru.businessinsights.backend.api.ApiModels.SourceConflictInput(
                            c.reason(), c.sourceIds(), c.resolved())).toList());
            int published = jdbc.update("""
                    INSERT INTO backend_api.event(event_id,content_hash,payload,published_at,catalog_store_id,catalog_generation,corpus_revision,event_content_sha256,ml_payload)
                    VALUES(?,?,?::jsonb,?,?,?,?,?,?::jsonb)
                    ON CONFLICT(event_id) DO UPDATE SET
                        current_version=backend_api.event.current_version + CASE
                            WHEN backend_api.event.event_content_sha256 IS DISTINCT FROM EXCLUDED.event_content_sha256 THEN 1 ELSE 0 END,
                        received_at=CASE WHEN backend_api.event.event_content_sha256 IS DISTINCT FROM EXCLUDED.event_content_sha256
                            THEN now() ELSE backend_api.event.received_at END,
                        content_hash=EXCLUDED.content_hash,payload=EXCLUDED.payload,published_at=EXCLUDED.published_at,
                        catalog_store_id=EXCLUDED.catalog_store_id,catalog_generation=EXCLUDED.catalog_generation,corpus_revision=EXCLUDED.corpus_revision,
                        event_content_sha256=EXCLUDED.event_content_sha256,ml_payload=EXCLUDED.ml_payload
                    WHERE backend_api.event.catalog_store_id=EXCLUDED.catalog_store_id
                      AND (backend_api.event.corpus_revision<>EXCLUDED.corpus_revision
                        OR (backend_api.event.event_content_sha256=EXCLUDED.event_content_sha256 AND backend_api.event.ml_payload=EXCLUDED.ml_payload))
                    """, event.eventId(), material.eventContentSha256(), write(publicEvent), event.publishedAt() == null ? null : java.sql.Timestamp.from(event.publishedAt()),
                    incoming.storeId(), incoming.generation(), incoming.revision(), material.eventContentSha256(), write(event));
            if (published != 1) throw conflict("CATALOG_CONTENT_CONFLICT", "Событие уже закреплено за другим неизменным содержимым или происхождением");
            jdbc.update("""
                    INSERT INTO backend_api.event_revision(event_id,version,content_hash,payload,published_at,received_at)
                    SELECT event_id,current_version,content_hash,payload,published_at,received_at
                    FROM backend_api.event WHERE event_id=?
                    ON CONFLICT(event_id,version) DO NOTHING
                    """, event.eventId());
            jdbc.update("UPDATE backend_api.operation SET stale=true,updated_at=now() WHERE event_id=? AND event_version < (SELECT current_version FROM backend_api.event WHERE event_id=?)",
                    event.eventId(), event.eventId());
        }
        if (!replay) jdbc.update("UPDATE backend_api.material_catalog_head SET store_id=?,generation=?,revision=? WHERE singleton", incoming.storeId(), incoming.generation(), incoming.revision());
        return incoming;
    }

    public CurrentEvent current(String eventId) {
        return findCurrent(eventId).orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "MATERIAL_EVENT_NOT_FOUND", "Материал каталога не найден"));
    }
    public java.util.Optional<CurrentEvent> findCurrent(String eventId) {
        return jdbc.query("SELECT ml_payload::text,event_content_sha256,catalog_store_id,catalog_generation,corpus_revision FROM backend_api.event WHERE event_id=? AND ml_payload IS NOT NULL",
                (rs, row) -> new CurrentEvent(read(rs.getString(1)), rs.getString(2), new CatalogToken(rs.getString(3), rs.getLong(4), rs.getString(5))), eventId)
                .stream().findFirst();
    }
    private static CatalogToken token(ResultSet rs) throws SQLException {
        return rs.getString("store_id") == null ? null : new CatalogToken(rs.getString("store_id"), rs.getLong("generation"), rs.getString("revision"));
    }
    private static SourceInput source(SourceRef source) { return new SourceInput(source.sourceId(), source.title(), source.date(), source.page(), source.url(),
            source.docId(), source.contentSha256(), source.excerpt()); }
    private String write(Object value) {
        try { return json.writeValueAsString(value); } catch (JsonProcessingException e) { throw new IllegalStateException("Cannot serialize material catalog", e); }
    }
    private NewsEvent read(String value) {
        try { return json.readValue(value, NewsEvent.class); } catch (JsonProcessingException e) { throw new IllegalStateException("Cannot read material catalog", e); }
    }
    private static ApiException conflict(String code, String message) { return new ApiException(HttpStatus.CONFLICT, code, message); }
}
