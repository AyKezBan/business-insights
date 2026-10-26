package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.EventInput;
import ru.businessinsights.backend.api.ApiModels.SourceInput;
import ru.businessinsights.backend.data.MaterialCatalogRepository;
import ru.businessinsights.backend.data.MaterialCatalogRepository.CatalogToken;
import ru.businessinsights.backend.data.MaterialCatalogRepository.MaterialEvent;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.testsupport.DisposablePostgres;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class MaterialCatalogLifecycleTest {
    private final ObjectMapper json = new ObjectMapper().findAndRegisterModules().disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private MaterialEvent event(String text, String hash) {
        return new MaterialEvent(new MlContracts.NewsEvent("logical:event", "Event", text, null,
                new MlContracts.SourceRef("opaque:source", MlContracts.SourceKind.news, "Source", null, null, "https://example.test/news"), List.of(), List.of()), hash);
    }
    @Test void trustedCasUpdatesStableEventAndRejectsLateOrOtherStorePublication() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = new MaterialCatalogRepository(db.jdbc(), json);
            var tx = new TransactionTemplate(new DataSourceTransactionManager(db.jdbc().getDataSource()));
            var r1 = new CatalogToken("store-one", 1, "a".repeat(64));
            var r2 = new CatalogToken("store-one", 2, "b".repeat(64));
            tx.execute(s -> repository.synchronize(null, r1, List.of(event("Не выросла", "1".repeat(64)))));
            tx.execute(s -> repository.synchronize(r1, r2, List.of(event("Выросла", "2".repeat(64)))));
            assertEquals(r2, repository.head());
            assertEquals("Выросла", repository.current("logical:event").event().text());
            assertEquals("2".repeat(64), repository.current("logical:event").eventContentSha256());
            tx.execute(s -> repository.synchronize(r1, r2, List.of(event("Выросла", "2".repeat(64)))));
            assertEquals(1, db.jdbc().queryForObject("SELECT count(*) FROM backend_api.event", Integer.class));
            assertThrows(ApiException.class, () -> tx.execute(s -> repository.synchronize(r2, r1, List.of(event("Не выросла", "1".repeat(64))))));
            assertThrows(ApiException.class, () -> tx.execute(s -> repository.synchronize(r2, new CatalogToken("other-store", 3, "c".repeat(64)), List.of())));
            assertEquals(r2, repository.head());
            assertEquals("Выросла", repository.current("logical:event").event().text());
        }
    }
    @Test void bankOnlyPublicationAdvancesHeadButStaleCasAndSameTokenDifferentHashFail() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = new MaterialCatalogRepository(db.jdbc(), json);
            var tx = new TransactionTemplate(new DataSourceTransactionManager(db.jdbc().getDataSource()));
            var r1 = new CatalogToken("store-one", 1, "a".repeat(64));
            var r2 = new CatalogToken("store-one", 2, "b".repeat(64));
            tx.execute(s -> repository.synchronize(null, r1, List.of(event("Не выросла", "1".repeat(64)))));
            tx.execute(s -> repository.synchronize(r1, r2, List.of(event("Не выросла", "1".repeat(64)))));
            assertEquals(r2, repository.head());
            assertEquals(r2, repository.current("logical:event").token());
            assertEquals("1".repeat(64), repository.current("logical:event").eventContentSha256());
            assertThrows(ApiException.class, () -> tx.execute(s -> repository.synchronize(r1, new CatalogToken("store-one", 3, "c".repeat(64)), List.of())));
            assertThrows(ApiException.class, () -> tx.execute(s -> repository.synchronize(r2, r2, List.of(event("Changed", "2".repeat(64))))));
            assertEquals("Не выросла", repository.current("logical:event").event().text());
        }
    }
    @Test void samePublicationCannotRewritePayloadByReusingItsHash() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = new MaterialCatalogRepository(db.jdbc(), json);
            var tx = new TransactionTemplate(new DataSourceTransactionManager(db.jdbc().getDataSource()));
            var r1 = new CatalogToken("store-one", 1, "a".repeat(64));
            tx.execute(s -> repository.synchronize(null, r1, List.of(event("Original", "1".repeat(64)))));
            assertThrows(ApiException.class, () -> tx.execute(s -> repository.synchronize(r1, r1, List.of(event("Tampered", "1".repeat(64))))));
            assertEquals("Original", repository.current("logical:event").event().text());
        }
    }

    @Test void concurrentLegacyRegistrationCannotBeOverwrittenByCatalogUpsert() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var selected = new java.util.concurrent.CountDownLatch(1);
            var release = new java.util.concurrent.CountDownLatch(1);
            var jdbc = new org.springframework.jdbc.core.JdbcTemplate(db.jdbc().getDataSource()) {
                @Override public List<java.util.Map<String,Object>> queryForList(String sql, Object... args) {
                    var rows = super.queryForList(sql, args);
                    if (sql.startsWith("SELECT catalog_store_id") && rows.isEmpty()) {
                        selected.countDown();
                        try { if (!release.await(5, java.util.concurrent.TimeUnit.SECONDS)) throw new IllegalStateException("Test registration timed out"); }
                        catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
                    }
                    return rows;
                }
            };
            var repository = new MaterialCatalogRepository(jdbc, json);
            var tx = new TransactionTemplate(new DataSourceTransactionManager(jdbc.getDataSource()));
            try (var executor = java.util.concurrent.Executors.newSingleThreadExecutor()) {
                var publication = executor.submit(() -> tx.execute(s -> repository.synchronize(null,
                        new CatalogToken("store-one", 1, "a".repeat(64)), List.of(event("Catalog", "1".repeat(64))))));
                try {
                    assertTrue(selected.await(5, java.util.concurrent.TimeUnit.SECONDS));
                    var legacy = new OperationStore(db.jdbc(), json);
                    legacy.registerEvent(new EventInput("logical:event", "Manual", "Original manual", null,
                            new SourceInput("manual-source", "Source", null, null, null), List.of(), List.of()), "manual-hash");
                    release.countDown();
                    var error = assertThrows(java.util.concurrent.ExecutionException.class, () -> publication.get(5, java.util.concurrent.TimeUnit.SECONDS));
                    assertInstanceOf(ApiException.class, error.getCause());
                    assertEquals("Original manual", legacy.event("logical:event").text());
                    assertNull(repository.head());
                } finally { release.countDown(); }
            }
        }
    }

    @Test void legacyManualEventRegistrationRemainsImmutable() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var store = new OperationStore(db.jdbc(), json);
            var input = new EventInput("manual", "Manual", "Original", null, new SourceInput("manual-source", "Source", null, null, null), List.of(), List.of());
            store.registerEvent(input, "original-hash");
            store.registerEvent(input, "original-hash");
            var error = assertThrows(ApiException.class, () -> store.registerEvent(input, "different-hash"));
            assertEquals("EVENT_ID_CONFLICT", error.code());
            assertEquals("Original", store.event("manual").text());
        }
    }
}
