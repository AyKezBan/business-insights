package ru.businessinsights.backend.data;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Reads the existing importer schema. No DDL or write privileges are needed.
 */
@Repository
class CompanyDataRepository {
    private final JdbcTemplate jdbc;

    CompanyDataRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    void lockSnapshot() {
        // Same order as importer TRUNCATE; acquire before SELECT creates a snapshot.
        // TRUNCATE is not MVCC safe, even at REPEATABLE READ isolation.
        jdbc.execute("""
                LOCK TABLE business_insights.payment_snapshot,
                    business_insights.person_company_link, business_insights.company_profile,
                    business_insights.person_ref, business_insights.company_ref,
                    business_insights.dataset_metadata IN ACCESS SHARE MODE
                """);
    }

    Map<String, Object> metadata() {
        var rows = query("""
                SELECT schema_version, dataset_sha256, loaded_at, source_manifest::text AS source_manifest,
                    payment_period_start, payment_period_end, payment_period_description, revenue_year, currency
                FROM business_insights.dataset_metadata WHERE singleton = TRUE
                """);
        if (rows.isEmpty()) {
            throw new ProfileDataException(ProfileDataException.Reason.DATASET_UNAVAILABLE,
                    "Набор данных ещё не загружен");
        }
        return rows.getFirst();
    }

    Map<String, Object> company(String companyId) {
        var rows = query("""
                SELECT r.synthetic_u AS company_id, r.profile_status, p.*
                FROM business_insights.company_ref r
                LEFT JOIN business_insights.company_profile p ON p.synthetic_u = r.synthetic_u
                WHERE r.synthetic_u = ?
                """, companyId);
        if (rows.isEmpty()) {
            throw new ProfileDataException(ProfileDataException.Reason.COMPANY_NOT_FOUND,
                    "Компания не найдена");
        }
        return rows.getFirst();
    }

    List<Map<String, Object>> links(String companyId) {
        // Other persons' identifiers are unnecessary for company provenance.
        return query("""
                SELECT source_row, synthetic_u, city_name, rc_name
                FROM business_insights.person_company_link WHERE synthetic_u = ? ORDER BY source_row
                """, companyId);
    }

    List<Map<String, Object>> group(String groupId) {
        if (groupId == null) {
            return List.of();
        }
        return query("""
                SELECT synthetic_u, source_row, group_id, mainokved_code, mainokved_name,
                    revenue_company, revenue_status, quality_issues
                FROM business_insights.company_profile WHERE group_id = ? ORDER BY synthetic_u
                """, groupId);
    }

    List<Map<String, Object>> payments(String companyId) {
        return query("""
                SELECT p.row_id, p.payer, p.beneficiary, p.pay_amt, p.pay_cnt,
                    p.ml_eligible, p.quality_issues,
                    pr.profile_status AS payer_profile_status,
                    br.profile_status AS beneficiary_profile_status,
                    cp.source_row AS counterparty_source_row,
                    cp.mainokved_code AS counterparty_okved_code,
                    cp.mainokved_name AS counterparty_okved_name,
                    cp.region AS counterparty_region, cp.group_id AS counterparty_group_id,
                    cp.quality_issues AS counterparty_quality_issues
                FROM business_insights.payment_snapshot p
                LEFT JOIN business_insights.company_ref pr ON pr.synthetic_u = p.payer
                LEFT JOIN business_insights.company_ref br ON br.synthetic_u = p.beneficiary
                LEFT JOIN business_insights.company_profile cp
                    ON cp.synthetic_u = CASE WHEN p.payer = ? THEN p.beneficiary ELSE p.payer END
                WHERE p.payer = ? OR p.beneficiary = ? ORDER BY p.row_id
                """, companyId, companyId, companyId);
    }

    List<CompanySummary> listCompanies(String personId, int limit, int offset) {
        return jdbc.query("""
                SELECT DISTINCT r.synthetic_u, r.profile_status, p.cl_type, p.mainokved_name, p.city, p.region
                FROM business_insights.person_company_link l
                JOIN business_insights.company_ref r ON r.synthetic_u = l.synthetic_u
                LEFT JOIN business_insights.company_profile p ON p.synthetic_u = r.synthetic_u
                WHERE l.synthetic_f = ? ORDER BY r.synthetic_u LIMIT ? OFFSET ?
                """, (rs, rowNum) -> new CompanySummary(rs.getString("synthetic_u"),
                rs.getString("profile_status"), rs.getString("cl_type"), rs.getString("mainokved_name"),
                rs.getString("city"), rs.getString("region")), personId, limit, offset);
    }

    boolean hasAccess(String personId, String companyId) {
        return Boolean.TRUE.equals(jdbc.queryForObject("""
                SELECT EXISTS (SELECT 1 FROM business_insights.person_company_link
                    WHERE synthetic_f = ? AND synthetic_u = ?)
                """, Boolean.class, personId, companyId));
    }

    private List<Map<String, Object>> query(String sql, Object... args) {
        return jdbc.query(sql, (rs, rowNum) -> row(rs), args);
    }

    private Map<String, Object> row(ResultSet rs) throws SQLException {
        Map<String, Object> values = new LinkedHashMap<>();
        var metadata = rs.getMetaData();
        for (int column = 1; column <= metadata.getColumnCount(); column++) {
            Object value = rs.getObject(column);
            if (value instanceof Array array) {
                try {
                    Object[] entries = (Object[]) array.getArray();
                    List<String> strings = new ArrayList<>(entries.length);
                    for (Object entry : entries) {
                        strings.add(entry == null ? null : entry.toString());
                    }
                    value = Collections.unmodifiableList(strings);
                } finally {
                    array.free();
                }
            } else if (value instanceof Timestamp timestamp) {
                value = timestamp.toInstant().toString();
            } else if (value instanceof java.sql.Date date) {
                value = date.toLocalDate().toString();
            }
            values.put(metadata.getColumnLabel(column), value);
        }
        return Collections.unmodifiableMap(values);
    }
}
