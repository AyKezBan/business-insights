package ru.businessinsights.backend.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.bind.Bindable;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.source.MapConfigurationPropertySource;
import java.time.Duration;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;

class BackendPropertiesBindingTest {
    @Test void applicationBindsCanonicalRecordAndRetainsTheDefaultFeedPolicy() {
        var source = new MapConfigurationPropertySource(Map.of(
                "backend.ml.base-url", "http://127.0.0.1:8000",
                "backend.ml.connect-timeout", "1s", "backend.ml.request-timeout", "2s",
                "backend.ml.max-response-bytes", "100000",
                "backend.jobs.workers", "1", "backend.jobs.queue-capacity", "4",
                "backend.jobs.shutdown-wait", "1s",
                "backend.auth.demo-token", "synthetic-test-token", "backend.auth.person-id", "F_1"));
        var bound = new Binder(source).bind("backend", Bindable.of(BackendProperties.class)).get();
        assertEquals(Duration.ofDays(30), bound.feed().maxAge());
        assertEquals(1, bound.jobs().workers());
        source.put("backend.feed.max-age", "45d");
        assertEquals(Duration.ofDays(45), new Binder(source)
                .bind("backend", Bindable.of(BackendProperties.class)).get().feed().maxAge());
    }
}
