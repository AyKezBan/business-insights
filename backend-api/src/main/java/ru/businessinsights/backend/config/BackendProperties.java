package ru.businessinsights.backend.config;

import java.net.URI;
import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("backend")
public record BackendProperties(Ml ml, Jobs jobs, Auth auth, Feed feed) {
    public BackendProperties {
        if (ml == null || jobs == null || auth == null) {
            throw new IllegalArgumentException("Missing backend configuration");
        }
        feed = feed == null ? new Feed(Duration.ofDays(30)) : feed;
    }

    public record Feed(Duration maxAge) {
        public Feed {
            if (maxAge == null || maxAge.isNegative() || maxAge.isZero() || maxAge.compareTo(Duration.ofDays(365)) > 0) {
                throw new IllegalArgumentException("Invalid backend.feed.max-age");
            }
        }
    }

    public record Ml(URI baseUrl, Duration connectTimeout, Duration requestTimeout, int maxResponseBytes) {
        public Ml {
            if (baseUrl == null || !("http".equals(baseUrl.getScheme()) || "https".equals(baseUrl.getScheme()))
                    || baseUrl.getHost() == null || baseUrl.getUserInfo() != null || baseUrl.getQuery() != null
                    || baseUrl.getFragment() != null || connectTimeout == null || requestTimeout == null
                    || connectTimeout.isNegative() || connectTimeout.isZero() || requestTimeout.isNegative()
                    || requestTimeout.isZero() || maxResponseBytes < 1024) {
                throw new IllegalArgumentException("Invalid backend.ml configuration");
            }
        }
    }

    public record Jobs(int workers, int queueCapacity, Duration shutdownWait) {
        public Jobs {
            if (workers < 1 || queueCapacity < 1 || shutdownWait == null || shutdownWait.isNegative()) {
                throw new IllegalArgumentException("Invalid backend.jobs configuration");
            }
        }
    }

    public record Auth(String demoToken, String personId) {
        public Auth {
            if (demoToken == null || personId == null || !personId.matches("F_\\d+")) {
                throw new IllegalArgumentException("Invalid backend.auth configuration");
            }
        }
    }
}
