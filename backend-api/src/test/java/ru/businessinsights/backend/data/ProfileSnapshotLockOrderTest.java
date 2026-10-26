package ru.businessinsights.backend.data;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.annotation.AnnotationTransactionAttributeSource;
import org.springframework.transaction.interceptor.TransactionInterceptor;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.AnalysisInput;
import ru.businessinsights.backend.api.ApiModels.EventInput;
import ru.businessinsights.backend.api.ApiModels.OperationView;
import ru.businessinsights.backend.api.ApiModels.Role;
import ru.businessinsights.backend.api.ApiModels.SourceInput;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.ml.MlClient;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.service.InsightMapper;
import ru.businessinsights.backend.service.InsightService;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.testsupport.DisposablePostgres;

import java.math.BigDecimal;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** Real importer tables, access query, Spring transactions, and PostgreSQL relation locks. */
class ProfileSnapshotLockOrderTest {
    // The same replacement statement/order as data-import/load_postgres.py; fixture rows are synthetic.
    private static final String TRUNCATE = """
            TRUNCATE business_insights.payment_snapshot, business_insights.person_company_link,
                business_insights.company_profile, business_insights.person_ref,
                business_insights.company_ref, business_insights.dataset_metadata
            """;
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private final AtomicBoolean pauseNextAccess = new AtomicBoolean();
    private final CountDownLatch accessRead = new CountDownLatch(1);
    private final CountDownLatch continueSubmission = new CountDownLatch(1);
    private final LinkedBlockingQueue<MlContracts.InsightRequest> sent = new LinkedBlockingQueue<>();
    private DisposablePostgres db;
    private JdbcTemplate jdbc;
    private OperationStore store;
    private InsightService service;

    @BeforeEach void createRealSchemaAndProxiedServices() throws Exception {
        db = DisposablePostgres.openOrSkip();
        jdbc = db.jdbc();
        var sql = Path.of(System.getProperty("basedir"), "..", "data-import", "sql");
        for (String file : List.of("01_schemas.sql", "02_tables.sql", "03_indexes.sql")) {
            jdbc.execute(Files.readString(sql.resolve(file)));
        }
        seed(jdbc, "P1", "1", 101, 11);
        var observedJdbc = new JdbcTemplate(jdbc.getDataSource()) {
            @Override public <T> T queryForObject(String sql, Class<T> type, Object... args) {
                T result = super.queryForObject(sql, type, args);
                if (sql.contains("SELECT EXISTS") && sql.contains("person_company_link")
                        && pauseNextAccess.compareAndSet(true, false)) {
                    accessRead.countDown();
                    try {
                        if (!continueSubmission.await(10, TimeUnit.SECONDS))
                            throw new IllegalStateException("Submission barrier was not released");
                    } catch (InterruptedException error) {
                        Thread.currentThread().interrupt();
                        throw new IllegalStateException(error);
                    }
                }
                return result;
            }
        };
        var profiles = transactional(new CompanyProfileService(new CompanyDataRepository(observedJdbc), mapper));
        store = new OperationStore(jdbc, mapper);
        var ml = mock(MlClient.class);
        when(ml.trace(anyString())).thenReturn(List.of());
        when(ml.generate(any())).thenAnswer(call -> {
            MlContracts.InsightRequest request = call.getArgument(0);
            sent.add(request);
            var response = mapper.createObjectNode().put("insight_id", "synthetic-lock-result")
                    .put("run_id", "synthetic-lock-run").put("event_id", request.event().eventId())
                    .put("company_id", request.profile().companyId()).put("role", request.role().name())
                    .put("status", "complete");
            return mapper.treeToValue(response, MlContracts.Insight.class);
        });
        var properties = new BackendProperties(
                new BackendProperties.Ml(URI.create("http://127.0.0.1:1"), Duration.ofSeconds(1), Duration.ofSeconds(1), 4096),
                new BackendProperties.Jobs(1, 4, Duration.ofSeconds(3)),
                new BackendProperties.Auth("synthetic-unused", "F_1"));
        service = transactional(new InsightService(profiles, ml, store, new InsightMapper(mapper), mapper,
                properties, new MaterialCatalogRepository(jdbc, mapper)));
        service.ready();
        service.event(new EventInput("event", "Synthetic event", "Synthetic sourced text", null,
                new SourceInput("source", "Synthetic source", null, null, null), List.of(), List.of()));
    }

    @AfterEach void closeOnlyOwnedResources() throws Exception {
        continueSubmission.countDown();
        try { if (service != null) service.close(); }
        finally { if (db != null) db.close(); }
    }

    @Test void submissionAndImporterReplacementCannotDeadlockOrMixDatasetVersions() throws Exception {
        pauseNextAccess.set(true);
        var importerPid = new AtomicInteger();
        try (var callers = Executors.newFixedThreadPool(2)) {
            var submission = callers.submit(() -> {
                try { return new Submission(service.analyze("F_1", input(Role.cfo, Map.of()), "race"), null); }
                catch (Exception error) { return new Submission(null, error); }
            });
            try {
                assertTrue(accessRead.await(5, TimeUnit.SECONDS), "Actual authorization SELECT did not complete");
                var replacement = callers.submit(() -> {
                    try (Connection connection = jdbc.getDataSource().getConnection()) {
                        connection.setAutoCommit(false);
                        try (var statement = connection.createStatement()) {
                            statement.execute("SET LOCAL statement_timeout = '8s'");
                            try (var pid = statement.executeQuery("SELECT pg_backend_pid()")) {
                                assertTrue(pid.next()); importerPid.set(pid.getInt(1));
                            }
                            // Importer reads metadata before replacing all six tables in one transaction.
                            statement.executeQuery("SELECT dataset_sha256 FROM business_insights.dataset_metadata").close();
                            statement.execute(TRUNCATE);
                        }
                        seed(new JdbcTemplate(new org.springframework.jdbc.datasource.SingleConnectionDataSource(connection, true)),
                                "P2", "2", 202, 22);
                        connection.commit();
                        return (Exception) null;
                    } catch (Exception error) { return error; }
                });
                // Observe the actual blocked relation lock, not a timing assumption or sleep.
                await().atMost(Duration.ofSeconds(5)).until(() -> importerPid.get() != 0 && Boolean.TRUE.equals(
                        jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=? AND locktype='relation' AND NOT granted)",
                                Boolean.class, importerPid.get())));
                continueSubmission.countDown();
                Submission result = submission.get(10, TimeUnit.SECONDS);
                Exception importFailure = replacement.get(10, TimeUnit.SECONDS);
                assertAll("Submission and replacement must both commit without SQLSTATE 40P01",
                        () -> assertNull(result.failure(), () -> "Submission failed: " + result.failure()),
                        () -> assertNull(importFailure, () -> "Importer failed: " + importFailure));
                assertNotNull(result.operation());
                var frozen = store.find(result.operation().operationId()).orElseThrow().profileSnapshot();
                assertAll("The accepted payload must be wholly the authorized P1 snapshot",
                        () -> assertEquals("P1", frozen.path("profile").path("region").asText()),
                        () -> assertEquals(new BigDecimal("101"), frozen.path("profile").path("revenue_rub").decimalValue()),
                        () -> assertEquals("1".repeat(64), frozen.path("provenance").path("dataset").path("dataset_sha256").asText()),
                        () -> assertEquals(new BigDecimal("11"), frozen.path("provenance").path("payments").path("outgoingNonSelfRub").decimalValue()));
                var request = sent.poll(5, TimeUnit.SECONDS);
                assertNotNull(request, "Committed operation must reach ML with its accepted snapshot");
                assertEquals("P1", request.profile().region());
                assertEquals(new BigDecimal("101"), request.profile().revenueRub());
                assertEquals("P2", jdbc.queryForObject("SELECT region FROM business_insights.company_profile WHERE synthetic_u='U_1'", String.class));
                assertEquals("2".repeat(64), jdbc.queryForObject("SELECT dataset_sha256 FROM business_insights.dataset_metadata", String.class));
            } finally { continueSubmission.countDown(); }
        }
    }

    @Test void authorizationStillPrecedesProfileValidation() {
        jdbc.update("UPDATE business_insights.dataset_metadata SET schema_version='incompatible'");
        var denied = assertThrows(ApiException.class,
                () -> service.analyze("F_other", input(Role.cfo, Map.of()), "unauthorized"));
        assertEquals("COMPANY_ACCESS_DENIED", denied.code());
        assertEquals(403, denied.status().value());
        assertThrows(ProfileDataException.class,
                () -> service.analyze("F_1", input(Role.cfo, Map.of()), "authorized"));
        assertEquals(0, jdbc.queryForObject("SELECT count(*) FROM backend_api.operation", Integer.class));
    }

    @Test void replayRechecksAccessWithoutReloadingMutableProfile() throws Exception {
        var first = service.analyze("F_1", input(Role.cfo, Map.of()), "replay");
        assertNotNull(sent.poll(5, TimeUnit.SECONDS));
        jdbc.update("UPDATE business_insights.dataset_metadata SET schema_version='incompatible'");
        var replay = service.analyze("F_1", input(Role.cfo, Map.of()), "replay");
        assertEquals(first.operationId(), replay.operationId());
        var changedRole = assertThrows(ApiException.class,
                () -> service.analyze("F_1", input(Role.owner, Map.of()), "replay"));
        assertEquals(409, changedRole.status().value());
        var changedInput = assertThrows(ApiException.class,
                () -> service.analyze("F_1", input(Role.cfo, Map.of("context", "changed")), "replay"));
        assertEquals(409, changedInput.status().value());
        jdbc.update("DELETE FROM business_insights.person_company_link WHERE synthetic_f='F_1'");
        var revoked = assertThrows(ApiException.class,
                () -> service.analyze("F_1", input(Role.cfo, Map.of()), "replay"));
        assertEquals("COMPANY_ACCESS_DENIED", revoked.code());
        assertEquals(1, jdbc.queryForObject("SELECT count(*) FROM backend_api.operation", Integer.class));
    }

    private AnalysisInput input(Role role, Map<String, String> provided) {
        return new AnalysisInput("U_1", role, "event", provided);
    }

    @SuppressWarnings("unchecked") private <T> T transactional(T target) {
        var factory = new ProxyFactory(target);
        factory.setProxyTargetClass(true);
        factory.addAdvice(new TransactionInterceptor(new DataSourceTransactionManager(jdbc.getDataSource()),
                new AnnotationTransactionAttributeSource()));
        return (T) factory.getProxy();
    }

    private void seed(JdbcTemplate target, String version, String hashCharacter, int revenue, int payment) {
        target.update("INSERT INTO business_insights.company_ref VALUES ('U_1','available'),('U_2','external')");
        target.update("""
                INSERT INTO business_insights.company_profile
                    (synthetic_u,source_row,cl_type,region,revenue_company,revenue_status,quality_issues)
                VALUES ('U_1',1,'CLIENT',?,?,'observed',ARRAY[]::text[])
                """, version, revenue);
        target.update("INSERT INTO business_insights.person_ref VALUES ('F_1')");
        target.update("INSERT INTO business_insights.person_company_link (source_row,synthetic_f,synthetic_u,rc_name) VALUES (1,'F_1','U_1',?)", version);
        target.update("INSERT INTO business_insights.payment_snapshot VALUES (1,'U_1','U_2',?,1,true,ARRAY[]::text[])", payment);
        target.update("""
                INSERT INTO business_insights.dataset_metadata
                    (schema_version,dataset_sha256,source_manifest,payment_period_description)
                VALUES ('0.3.0',?,'{}'::jsonb,?)
                """, hashCharacter.repeat(64), version);
    }

    private record Submission(OperationView operation, Exception failure) {}
}
