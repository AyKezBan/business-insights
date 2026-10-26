package ru.businessinsights.backend.testsupport;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Assumptions;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.net.URI;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.Locale;
import java.util.Properties;
import java.util.UUID;

/**
 * Owns one generated database in an explicitly selected local test cluster.
 * BI_JDBC_TEST_URL must point at its postgres administration database; credentials
 * use BI_JDBC_TEST_USER / BI_JDBC_TEST_PASSWORD (both default to postgres).
 * Never migrates or truncates the database named by the environment variable.
 */
public final class DisposablePostgres implements AutoCloseable {
    private final String adminUrl;
    private final String databaseName;
    private final Properties credentials;
    private final DriverManagerDataSource dataSource;
    private boolean closed;

    public static DisposablePostgres openOrSkip() throws SQLException {
        String adminUrl = System.getenv("BI_JDBC_TEST_URL");
        Assumptions.assumeTrue(adminUrl != null && !adminUrl.isBlank(),
                "NOT_RUN: BI_JDBC_TEST_URL is required for actual PostgreSQL integration tests");
        return new DisposablePostgres(adminUrl,
                System.getenv().getOrDefault("BI_JDBC_TEST_USER", "postgres"),
                System.getenv().getOrDefault("BI_JDBC_TEST_PASSWORD", "postgres"));
    }

    private DisposablePostgres(String adminUrl, String user, String password) throws SQLException {
        URI uri = validateAdminUrl(adminUrl);
        this.adminUrl = adminUrl;
        this.databaseName = "bi_c3_test_" + UUID.randomUUID().toString().replace("-", "");
        this.credentials = new Properties();
        credentials.setProperty("user", user);
        credentials.setProperty("password", password);
        String databaseUrl = "jdbc:postgresql://" + uri.getRawAuthority() + "/" + databaseName;
        this.dataSource = new DriverManagerDataSource(databaseUrl, credentials);
        try (Connection admin = DriverManager.getConnection(adminUrl, credentials);
             var statement = admin.createStatement()) {
            statement.execute("CREATE DATABASE " + databaseName);
        }
        try {
            Flyway.configure().dataSource(dataSource).schemas("backend_api").defaultSchema("backend_api")
                    .createSchemas(true).locations("classpath:db/migration").load().migrate();
        } catch (RuntimeException migrationFailure) {
            try {
                close();
            } catch (SQLException cleanupFailure) {
                migrationFailure.addSuppressed(cleanupFailure);
            }
            throw migrationFailure;
        }
    }

    private static URI validateAdminUrl(String value) {
        if (!value.startsWith("jdbc:postgresql://")) {
            throw new IllegalArgumentException("BI_JDBC_TEST_URL must be an explicit local PostgreSQL JDBC URL");
        }
        URI uri;
        try {
            uri = URI.create(value.substring("jdbc:".length()));
        } catch (IllegalArgumentException invalidUrl) {
            throw new IllegalArgumentException("BI_JDBC_TEST_URL is not a valid local PostgreSQL URL");
        }
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
        if (!(host.equals("localhost") || host.equals("127.0.0.1") || host.equals("[::1]") || host.equals("::1"))
                || !"/postgres".equals(uri.getPath()) || uri.getUserInfo() != null
                || uri.getFragment() != null || uri.getRawQuery() != null) {
            throw new IllegalArgumentException("BI_JDBC_TEST_URL must target /postgres on a dedicated loopback test cluster without embedded credentials or query options");
        }
        return uri;
    }

    public JdbcTemplate jdbc() {
        return new JdbcTemplate(dataSource);
    }

    @Override
    public void close() throws SQLException {
        if (closed) {
            return;
        }
        // The name is generated internally, not accepted from a request or URL.
        try (Connection admin = DriverManager.getConnection(adminUrl, credentials);
             var statement = admin.createStatement()) {
            statement.execute("DROP DATABASE " + databaseName + " WITH (FORCE)");
            closed = true;
        }
    }
}
