package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.interceptor.TransactionInterceptor;
import org.springframework.transaction.annotation.AnnotationTransactionAttributeSource;
import org.springframework.aop.framework.ProxyFactory;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.data.MaterialCatalogRepository;
import ru.businessinsights.backend.data.MaterialCatalogRepository.CatalogToken;
import ru.businessinsights.backend.data.MaterialCatalogRepository.MaterialEvent;
import ru.businessinsights.backend.ml.MaterialCatalogClient;
import ru.businessinsights.backend.ml.MaterialCatalogClient.CatalogPage;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.ml.MlException;
import ru.businessinsights.backend.testsupport.DisposablePostgres;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class MaterialCatalogServiceTest {
    private final ObjectMapper json = new ObjectMapper().findAndRegisterModules().disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private final CatalogToken r1 = new CatalogToken("fixture-store", 1, "a".repeat(64));
    private final CatalogToken r2 = new CatalogToken("fixture-store", 2, "b".repeat(64));

    private MaterialEvent event(String id) {
        return new MaterialEvent(new MlContracts.NewsEvent(id, "Материал " + id, "Проверяемый текст " + id, null,
                new MlContracts.SourceRef("source:" + id, MlContracts.SourceKind.news, "Источник", null, null,
                        "https://example.test/" + id), List.of(), List.of("economy")), "1".repeat(64));
    }
    private MaterialCatalogRepository repository(DisposablePostgres db) {
        var factory = new ProxyFactory(new MaterialCatalogRepository(db.jdbc(), json));
        factory.setProxyTargetClass(true);
        factory.addAdvice(new TransactionInterceptor(new DataSourceTransactionManager(db.jdbc().getDataSource()), new AnnotationTransactionAttributeSource()));
        return (MaterialCatalogRepository) factory.getProxy();
    }
    private MaterialCatalogService service(MaterialCatalogClient client, MaterialCatalogRepository repository) {
        return new MaterialCatalogService(client, repository, new InsightMapper(json), json);
    }
    @Test void completeTrustedCatalogIsPublishedBeforeLocalPagingWithoutCreatingAnalysis() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = repository(db);
            var client = mock(MaterialCatalogClient.class);
            when(client.page(null, null, 100)).thenReturn(new CatalogPage(r1, List.of(event("one")), "opaque:next"));
            when(client.page(r1.revision(), "opaque:next", 100)).thenReturn(new CatalogPage(r1, List.of(event("two")), null));
            var result = service(client, repository).events(1, 1, null);
            assertEquals(r1.revision(), result.catalogToken().revision());
            assertEquals(2, result.total());
            assertEquals("two", result.items().getFirst().eventId());
            assertEquals("source:two", result.items().getFirst().source().sourceId());
            assertEquals(r1.revision(), result.items().getFirst().corpusRevision());
            assertEquals("1".repeat(64), result.items().getFirst().eventContentSha256());
            assertEquals(r1, repository.head());
            assertEquals(r1, repository.current("one").token());
            assertEquals(0, db.jdbc().queryForObject("SELECT count(*) FROM backend_api.operation", Integer.class));
            verify(client).page(r1.revision(), "opaque:next", 100);
        }
    }
    @Test void tokenDriftAndDuplicateEventsNeverPublishPartialCatalog() throws Exception {
        for (boolean drift : List.of(true, false)) {
            try (var db = DisposablePostgres.openOrSkip()) {
                var repository = repository(db);
                var client = mock(MaterialCatalogClient.class);
                when(client.page(null, null, 100)).thenReturn(new CatalogPage(r1, List.of(event("one")), "next"));
                when(client.page(r1.revision(), "next", 100)).thenReturn(new CatalogPage(drift ? r2 : r1,
                        List.of(event(drift ? "two" : "one")), null));
                assertThrows(MlException.class, () -> service(client, repository).events(20, 0, null));
                assertNull(repository.head());
                assertEquals(0, db.jdbc().queryForObject("SELECT count(*) FROM backend_api.event", Integer.class));
            }
        }
    }
    @Test void failedLaterPagePreservesPreviouslyPublishedCatalog() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = repository(db);
            repository.synchronize(null, r1, List.of(event("old")));
            var client = mock(MaterialCatalogClient.class);
            when(client.page(null, null, 100)).thenReturn(new CatalogPage(r2, List.of(event("new")), "next"));
            when(client.page(r2.revision(), "next", 100)).thenThrow(new MlException(MlException.Code.UNAVAILABLE, "fixture outage", true));
            assertThrows(MlException.class, () -> service(client, repository).events(20, 0, null));
            assertEquals(r1, repository.head());
            assertTrue(repository.findCurrent("new").isEmpty());
        }
    }
    @Test void repeatedCursorCannotLoopOrPublish() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = repository(db);
            var client = mock(MaterialCatalogClient.class);
            when(client.page(null, null, 100)).thenReturn(new CatalogPage(r1, List.of(event("one")), "next"));
            when(client.page(r1.revision(), "next", 100)).thenReturn(new CatalogPage(r1, List.of(event("two")), "next"));
            assertThrows(MlException.class, () -> service(client, repository).events(20, 0, null));
            assertNull(repository.head());
            verify(client, times(1)).page(r1.revision(), "next", 100);
        }
    }
    @Test void staleFetchCannotRollBackConcurrentPublication() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = repository(db);
            var client = mock(MaterialCatalogClient.class);
            when(client.page(null, null, 100)).thenAnswer(invocation -> {
                repository.synchronize(null, r2, List.of(event("new")));
                return new CatalogPage(r1, List.of(event("old")), null);
            });
            assertThrows(ApiException.class, () -> service(client, repository).events(20, 0, null));
            assertEquals(r2, repository.head());
            assertTrue(repository.findCurrent("old").isEmpty());
        }
    }
    @Test void browserPagingRejectsChangedRevisionWithoutSilentlyChangingSelection() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = repository(db);
            var client = mock(MaterialCatalogClient.class);
            when(client.page(null, null, 100)).thenReturn(new CatalogPage(r2, List.of(event("new")), null));
            var error = assertThrows(ApiException.class, () -> service(client, repository).events(20, 20, r1.revision()));
            assertEquals("CATALOG_CHANGED", error.code());
            assertNull(repository.head());
        }
    }

    @Test void publicCatalogUsesExistingAuthenticationAndCamelCaseDto() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var repository = repository(db);
            var client = mock(MaterialCatalogClient.class);
            when(client.page(null, null, 100)).thenReturn(new CatalogPage(r1, List.of(event("one")), null));
            var properties = new ru.businessinsights.backend.config.BackendProperties(
                    new ru.businessinsights.backend.config.BackendProperties.Ml(java.net.URI.create("http://127.0.0.1:9"), java.time.Duration.ofSeconds(1), java.time.Duration.ofSeconds(2), 100000),
                    new ru.businessinsights.backend.config.BackendProperties.Jobs(1, 2, java.time.Duration.ofSeconds(1)),
                    new ru.businessinsights.backend.config.BackendProperties.Auth("synthetic-token", "F_1"));
            var mvc = org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup(
                            new ru.businessinsights.backend.api.BackendController(mock(InsightService.class), service(client, repository)))
                    .setControllerAdvice(new ru.businessinsights.backend.api.ApiErrorHandler())
                    .setMessageConverters(new org.springframework.http.converter.json.MappingJackson2HttpMessageConverter(json))
                    .addFilters(new ru.businessinsights.backend.auth.DemoAuthFilter(properties, json)).build();
            mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get("/api/v1/materials/events").servletPath("/api/v1/materials/events"))
                    .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isUnauthorized());
            verifyNoInteractions(client);
            var response = mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get("/api/v1/materials/events").servletPath("/api/v1/materials/events")
                            .header("Authorization", "Bearer synthetic-token"))
                    .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isOk()).andReturn();
            var body = json.readTree(response.getResponse().getContentAsString());
            assertEquals(r1.revision(), body.path("catalogToken").path("revision").asText());
            assertEquals("one", body.path("items").get(0).path("eventId").asText());
            assertEquals("source:one", body.path("items").get(0).path("source").path("sourceId").asText());
            assertFalse(body.has("catalog_token"));
        }
    }

    @Test void catalogOutageReturnsExplicitRetryableProblem() throws Exception {
        var materials = mock(MaterialCatalogService.class);
        when(materials.events(50, 0, null)).thenThrow(new MlException(MlException.Code.UNAVAILABLE, "Catalog unavailable", true));
        var mvc = org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup(
                        new ru.businessinsights.backend.api.BackendController(mock(InsightService.class), materials))
                .setControllerAdvice(new ru.businessinsights.backend.api.ApiErrorHandler()).build();
        var response = mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get("/api/v1/materials/events"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isServiceUnavailable()).andReturn();
        var body = json.readTree(response.getResponse().getContentAsString());
        assertEquals("ML_UNAVAILABLE", body.path("code").asText());
        assertTrue(body.path("retryable").asBoolean());
    }
}
