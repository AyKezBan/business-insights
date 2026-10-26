package ru.businessinsights.backend.testsupport;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;

class EventVersionMigrationTest {
    @Test void populatedCatalogV2UpgradesWithoutLosingFrozenOperationsOrFeedback() throws Exception {
        try (var db = DisposablePostgres.openOrSkip("2")) {
            var jdbc = db.jdbc();
            UUID id = UUID.randomUUID();
            jdbc.update("INSERT INTO backend_api.event(event_id,content_hash,payload,published_at) VALUES('old','old-hash','{\"title\":\"original\"}'::jsonb,now())");
            jdbc.update("""
                    INSERT INTO backend_api.operation(id,owner_id,company_id,role,kind,event_id,request_hash,status,payload,profile_snapshot,result)
                    VALUES(?,'F_1','U_1','cfo','ANALYSIS','old','key-hash','SUCCEEDED','{}','{"corpusRevision":"original"}','{"headline":"kept"}')
                    """, id);
            jdbc.update("INSERT INTO backend_api.feedback(operation_id,owner_id,rating,comment) VALUES(?,'F_1','USEFUL','kept')", id);
            Flyway.configure().dataSource(jdbc.getDataSource()).schemas("backend_api").defaultSchema("backend_api")
                    .locations("classpath:db/migration").load().migrate();
            assertEquals("3", jdbc.queryForObject("SELECT version FROM backend_api.flyway_schema_history WHERE success ORDER BY installed_rank DESC LIMIT 1", String.class));
            assertEquals(1L, jdbc.queryForObject("SELECT version FROM backend_api.event_revision WHERE event_id='old'", Long.class));
            assertEquals(1L, jdbc.queryForObject("SELECT event_version FROM backend_api.operation WHERE id=?", Long.class, id));
            assertEquals("original", jdbc.queryForObject("SELECT profile_snapshot->>'corpusRevision' FROM backend_api.operation WHERE id=?", String.class, id));
            assertEquals("kept", jdbc.queryForObject("SELECT result->>'headline' FROM backend_api.operation WHERE id=?", String.class, id));
            assertEquals("USEFUL", jdbc.queryForObject("SELECT rating FROM backend_api.feedback WHERE operation_id=?", String.class, id));
            assertNotNull(jdbc.queryForObject("SELECT expires_at FROM backend_api.operation WHERE id=?", java.sql.Timestamp.class, id));
            assertEquals(1, jdbc.queryForObject("SELECT count(*) FROM backend_api.material_catalog_head", Integer.class));
        }
    }
}
