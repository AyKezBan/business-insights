package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import ru.businessinsights.backend.config.BackendProperties;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.jupiter.api.Assertions.*;

class HttpMlNewsTest {
    @Test void offsetNewsUsesItsOwnEndpointAndPreservesExtendedSourceMetadata() throws Exception {
        var json = new ObjectMapper().findAndRegisterModules();
        var body = json.createObjectNode().put("corpus_revision", "a".repeat(64));
        body.putNull("next_offset");
        var event = body.putArray("items").addObject().put("event_id", "opaque:event")
                .put("title", "Synthetic event").put("text", "Synthetic original text")
                .put("published_date", "2026-10-26").put("published_precision", "date");
        event.putObject("source").put("source_id", "opaque:source").put("kind", "news")
                .put("title", "Original source").put("publisher_id", "eia").put("language", "en")
                .put("content_sha256", "d".repeat(64)).put("excerpt", "Synthetic original text");
        var requested = new AtomicReference<URI>();
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/materials/news", exchange -> {
            requested.set(exchange.getRequestURI());
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            try (var output = exchange.getResponseBody()) { output.write(bytes); }
        });
        server.start();
        try {
            var properties = new BackendProperties(new BackendProperties.Ml(
                    URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
                    Duration.ofSeconds(1), Duration.ofSeconds(2), 100000),
                    new BackendProperties.Jobs(1, 1, Duration.ZERO), new BackendProperties.Auth("", "F_1"));
            var client = new HttpMlClient(json, properties);
            var page = client.news(1, 0);
            assertEquals("/v1/materials/news", requested.get().getPath());
            assertEquals("limit=1&offset=0", requested.get().getQuery());
            assertEquals("eia", page.items().getFirst().source().publisherId());
            assertEquals("d".repeat(64), page.items().getFirst().source().contentSha256());
            assertEquals("date", page.items().getFirst().publishedPrecision());
            body.put("next_offset", 3);
            assertEquals(MlException.Code.INVALID_RESPONSE,
                    assertThrows(MlException.class, () -> client.news(1, 0)).code());
        } finally { server.stop(0); }
    }
}
