package ru.businessinsights.backend.data;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class CompanyProfileEvidenceTest {
    @org.junit.jupiter.api.Test void incompletePaymentDatesStayUnknown() {
        var repository = mock(CompanyDataRepository.class);
        when(repository.metadata()).thenReturn(Map.of("schema_version", "0.3.0", "revenue_year", 2025, "currency", "RUB", "source_manifest", "{}",
                "payment_period_description", "С 1 января; год начала и дата окончания не установлены"));
        when(repository.company("U_1")).thenReturn(Map.of("profile_status", "available", "source_row", 1, "cl_type", "CLIENT"));
        when(repository.group(null)).thenReturn(List.of()); when(repository.links("U_1")).thenReturn(List.of()); when(repository.payments("U_1")).thenReturn(List.of());
        var snapshot = new CompanyProfileService(repository, new ObjectMapper().findAndRegisterModules()).loadProfile("U_1", Map.of());
        assertNull(snapshot.profile().paymentPeriod());
        assertTrue(snapshot.profile().missingFields().contains("payment_period"));
        assertTrue(snapshot.qualityIssues().contains("payment_period_and_coverage_unknown"));
    }

    @ParameterizedTest
    @CsvSource({"0,observed,known,0", "999,observed,known,999", "1000,placeholder,placeholder,null", "-1,invalid,invalid,null"})
    void preservesRevenueMeaningAndFtsUncertainty(String value, String status, String expectedStatus, String expectedRevenue) {
        var repository = mock(CompanyDataRepository.class);
        when(repository.metadata()).thenReturn(Map.of("schema_version", "0.3.0", "revenue_year", 2025, "currency", "RUB", "source_manifest", "{}",
                "payment_period_description", "2025-Q4", "payment_period_start", "2025-10-01", "payment_period_end", "2025-12-31"));
        var company = new HashMap<String,Object>();
        company.put("profile_status", "available"); company.put("source_row", 1); company.put("cl_type", "CLIENT");
        company.put("revenue_company", new BigDecimal(value)); company.put("revenue_status", status); company.put("fts_flag", false);
        when(repository.company("U_1")).thenReturn(company);
        when(repository.group(null)).thenReturn(List.of()); when(repository.links("U_1")).thenReturn(List.of()); when(repository.payments("U_1")).thenReturn(List.of());
        var snapshot = new CompanyProfileService(repository, new ObjectMapper().findAndRegisterModules()).loadProfile("U_1", Map.of());
        var profile = snapshot.profile();
        assertEquals(expectedStatus, profile.revenueStatus());
        assertEquals("null".equals(expectedRevenue) ? null : new BigDecimal(expectedRevenue), profile.revenueRub());
        assertEquals(2025, profile.revenueYear()); assertEquals("company", profile.revenueScope());
        assertEquals(false, profile.foreignTradeDataAvailable()); assertNull(profile.foreignTrade());
        assertEquals("2025-Q4", profile.paymentPeriod());
        assertTrue(profile.profileQualityNotes().contains("fts_absence_does_not_establish_no_trade"));
        assertFalse(snapshot.qualityIssues().contains("payment_period_and_coverage_unknown"), "known payment period must not be described as unknown");
        assertTrue(snapshot.qualityIssues().contains("payment_coverage_unknown"));
    }
}
