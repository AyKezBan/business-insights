package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import jakarta.validation.Validation;
import org.junit.jupiter.api.Test;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.annotation.AnnotationTransactionAttributeSource;
import org.springframework.transaction.interceptor.TransactionInterceptor;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.*;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.data.CompanyProfileService;
import ru.businessinsights.backend.data.MaterialCatalogRepository;
import ru.businessinsights.backend.data.MaterialCatalogRepository.*;
import ru.businessinsights.backend.data.ProfileSnapshot;
import ru.businessinsights.backend.ml.MaterialCatalogClient;
import ru.businessinsights.backend.ml.MaterialCatalogClient.CatalogPage;
import ru.businessinsights.backend.ml.MlClient;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.testsupport.DisposablePostgres;
import java.net.URI;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class FeedRefreshIntegrationTest {
    private final ObjectMapper json = new ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

    @Test void refreshUsesTrustedCatalogAndReplaysOperationsWithFrozenQueuedContext() throws Exception {
        try (var db = DisposablePostgres.openOrSkip(); var validators = Validation.buildDefaultValidatorFactory()) {
            var profiles = mock(CompanyProfileService.class);
            var profile = new AtomicReference<>(profile("P1"));
            when(profiles.hasAccess("F_1", "U_1")).thenReturn(true);
            when(profiles.loadProfile(eq("U_1"), anyMap())).thenAnswer(call -> profile.get());
            var repository = transactional(new MaterialCatalogRepository(db.jdbc(), json), db);
            var store = transactional(new OperationStore(db.jdbc(), json), db);
            var client = mock(MaterialCatalogClient.class);
            var token = new CatalogToken("fixture-store", 1, "a".repeat(64));
            var publication = Instant.now().minus(Duration.ofDays(1));
            when(client.page(null, null, 100)).thenReturn(new CatalogPage(token,
                    List.of(material("opaque:one/ю?x=1#frag", publication), material("two", publication.minusSeconds(1))), null));
            var views = new InsightMapper(json);
            var materials = new MaterialCatalogService(client, repository, views, json);
            var ml = mock(MlClient.class);
            var calls = new AtomicInteger();
            var firstEntered = new CountDownLatch(1);
            var release = new CountDownLatch(1);
            var requests = new CopyOnWriteArrayList<MlContracts.InsightRequest>();
            when(ml.trace(anyString())).thenReturn(List.of());
            when(ml.generate(any())).thenAnswer(call -> {
                MlContracts.InsightRequest request = call.getArgument(0);
                requests.add(request);
                if (calls.incrementAndGet() == 1) {
                    firstEntered.countDown();
                    if (!release.await(15, TimeUnit.SECONDS)) throw new IllegalStateException("Fixture worker was not released");
                }
                return response(request);
            });
            var properties = new BackendProperties(new BackendProperties.Ml(URI.create("http://127.0.0.1:1"),
                    Duration.ofSeconds(1), Duration.ofSeconds(1), 100000),
                    new BackendProperties.Jobs(1, 4, Duration.ofSeconds(3)), new BackendProperties.Auth("", "F_1"));
            var service = transactional(new InsightService(profiles, ml, store, views, json, properties,
                    repository, new EventRelevance(properties), validators.getValidator(), materials), db);
            service.ready();
            try {
                var input = new FeedRefreshInput(Role.cfo, 20, 0, 8, Map.of());
                assertEquals("COMPANY_ACCESS_DENIED", assertThrows(ApiException.class,
                        () -> service.refresh("F_other", "U_1", input)).code());
                verifyNoInteractions(client);
                var first = service.refresh("F_1", "U_1", input);
                assertTrue(firstEntered.await(5, TimeUnit.SECONDS));
                assertEquals(token.revision(), first.corpusRevision());
                assertNull(first.nextOffset());
                assertEquals(2, first.candidates().size());
                assertTrue(first.candidates().stream().anyMatch(c -> "opaque:one/ю?x=1#frag".equals(c.eventId())));
                var ids = first.candidates().stream().map(c -> c.operation().operationId()).collect(Collectors.toSet());
                var retry = service.refresh("F_1", "U_1", input);
                assertTrue(retry.candidates().stream().allMatch(c -> "EXISTING_OPERATION".equals(c.decision())));
                assertEquals(ids, retry.candidates().stream().map(c -> c.operation().operationId()).collect(Collectors.toSet()));
                assertEquals(2, db.jdbc().queryForObject("SELECT count(*) FROM backend_api.operation", Integer.class));
                assertEquals(1, calls.get());
                for (var id : ids) {
                    var op = store.find(id).orElseThrow();
                    assertEquals(1, op.eventVersion());
                    assertEquals("P1", op.profileSnapshot().path("profile").path("region").asText());
                    assertEquals(token.revision(), op.payload().path("request").path("corpus_revision").asText());
                }
                profile.set(profile("P2"));
                release.countDown();
                await().atMost(Duration.ofSeconds(8)).until(() -> ids.stream()
                        .allMatch(id -> "SUCCEEDED".equals(store.find(id).orElseThrow().status())));
                assertEquals(2, calls.get());
                assertTrue(requests.stream().allMatch(r -> "P1".equals(r.profile().region())));
                assertTrue(requests.stream().allMatch(r -> token.revision().equals(r.corpusRevision())));
                assertEquals(2, service.feed("F_1", "U_1", Role.cfo, 20, 0, false).items().size());
                var id = ids.iterator().next();
                service.feedback("F_1", id, new FeedbackInput(Rating.USEFUL, "kept"));
                assertEquals("USEFUL", service.insight("F_1", id).feedback().rating());
                verify(ml, never()).news(anyInt(), anyInt());
            } finally {
                release.countDown();
                service.close();
            }
        }
    }

    private MaterialEvent material(String id, Instant date) {
        var source = new MlContracts.SourceRef("source:" + id, MlContracts.SourceKind.news, "Source", null, null,
                "https://example.test/" + id, "doc:" + id, "d".repeat(64), "Synthetic source text");
        return new MaterialEvent(new MlContracts.NewsEvent(id, "Synthetic rate event", "Synthetic news text", date,
                source, List.of(), List.of("cost_of_debt")), "e".repeat(64));
    }

    private ProfileSnapshot profile(String region) throws Exception {
        var value = json.createObjectNode().put("company_id", "U_1").put("region", region);
        value.putObject("products").put("loans", true);
        return new ProfileSnapshot(json.treeToValue(value, MlContracts.CompanyProfile.class),
                json.createObjectNode().put("fixture", region), List.of(), true);
    }

    private MlContracts.Insight response(MlContracts.InsightRequest request) throws Exception {
        var value = json.createObjectNode().put("insight_id", "synthetic_" + request.event().eventId())
                .put("run_id", "run_fixture").put("company_id", request.profile().companyId())
                .put("event_id", request.event().eventId()).put("role", request.role().name())
                .put("status", "complete").put("outcome", "full").put("headline", "Synthetic result")
                .put("request_id", request.requestId()).put("corpus_revision", request.corpusRevision())
                .put("event_content_sha256", request.eventContentSha256()).put("as_of", request.asOf().toString())
                .put("generated_at", Instant.now().toString()).put("impact_level", "medium");
        return json.treeToValue(value, MlContracts.Insight.class);
    }

    @SuppressWarnings("unchecked")
    private static <T> T transactional(T target, DisposablePostgres db) {
        var proxy = new ProxyFactory(target);
        proxy.setProxyTargetClass(true);
        proxy.addAdvice(new TransactionInterceptor(new DataSourceTransactionManager(db.jdbc().getDataSource()),
                new AnnotationTransactionAttributeSource()));
        return (T) proxy.getProxy();
    }
}
