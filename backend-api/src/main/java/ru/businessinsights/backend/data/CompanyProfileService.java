package ru.businessinsights.backend.data;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import ru.businessinsights.backend.ml.MlContracts.CompanyProfile;
import ru.businessinsights.backend.ml.MlContracts.Counterparty;
import ru.businessinsights.backend.ml.MlContracts.Products;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.MathContext;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

@Service
public class CompanyProfileService {
    private static final String DATA_REVISION = "f44b69f0d124e8e4c33e5301b629413618090fa5";
    private static final BigDecimal PLACEHOLDER = new BigDecimal("1000");
    private final CompanyDataRepository repository;
    private final ObjectMapper mapper;

    public CompanyProfileService(CompanyDataRepository repository, ObjectMapper mapper) {
        this.repository = repository;
        this.mapper = mapper;
    }

    @Transactional(readOnly = true)
    public List<CompanySummary> listCompanies(String personId, int limit, int offset) {
        if (limit < 1 || limit > 100 || offset < 0) {
            throw new IllegalArgumentException("Invalid pagination");
        }
        return List.copyOf(repository.listCompanies(personId, limit, offset));
    }

    @Transactional(readOnly = true)
    public boolean hasAccess(String personId, String companyId) {
        return repository.hasAccess(personId, companyId);
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ, timeout = 30)
    public ProfileSnapshot loadProfile(String companyId, Map<String, String> userProvided) {
        repository.lockSnapshot();
        Map<String, Object> metadata = repository.metadata();
        validateMetadata(metadata);
        Map<String, Object> company = repository.company(companyId);
        gate(company);
        var issues = new LinkedHashSet<>(strings(company.get("quality_issues")));
        issues.add("payment_period_and_coverage_unknown");
        String groupId = text(company, "group_id");
        var group = repository.group(groupId);
        var links = repository.links(companyId);
        var paymentRows = repository.payments(companyId);
        var flows = flows(companyId, groupId, paymentRows, issues);
        String fns = text(company, "amount_workers_fns_gradation");
        String spark = text(company, "amount_workers_spark_gradation");
        String employees = fns == null ? spark : spark == null || fns.equals(spark) ? fns : null;
        if (fns != null && spark != null && !fns.equals(spark)) {
            issues.add("conflicting_employee_gradations");
        }
        Boolean fts = Boolean.TRUE.equals(company.get("fts_flag")) ? Boolean.TRUE : null;
        if (Boolean.FALSE.equals(company.get("fts_flag"))) {
            issues.add("fts_absence_does_not_establish_no_trade");
        }
        if (!strings(company.get("fts_countries")).isEmpty() && Boolean.FALSE.equals(company.get("fts_flag"))) {
            issues.add("countries_present_without_fts_flag");
        }
        BigDecimal revenue = observedRevenue(company);
        if (revenue == null) {
            issues.add("company_revenue_" + ("observed".equals(text(company, "revenue_status")) ? "invalid_or_placeholder_in_storage" : text(company, "revenue_status")));
        }
        if (groupId != null) {
            issues.add("group_composition_and_consolidated_revenue_unknown");
        }
        var memberOkveds = new LinkedHashSet<String>();
        for (var member : group) {
            if (!companyId.equals(text(member, "synthetic_u")) && text(member, "mainokved_code") != null) {
                memberOkveds.add(text(member, "mainokved_code"));
            }
        }
        Products products = new Products(bool(company, "cl_product_rko_mark"), bool(company, "cl_product_gar_mark"),
                bool(company, "cl_product_loa_mark"), bool(company, "cl_product_lea_mark"),
                bool(company, "cl_product_dep_mark"), bool(company, "cl_product_zpp_mark"), bool(company, "factoring_mark"));
        var missing = new LinkedHashSet<String>();
        missing.add("group_revenue_rub");
        missing.add("group_composition");
        missing.add("payment_period");
        missing.add("payment_coverage");
        missing.add("industry");
        missingIfNull(missing, "okved_code", text(company, "mainokved_code"));
        missingIfNull(missing, "okved_name", text(company, "mainokved_name"));
        missingIfNull(missing, "revenue_rub", revenue);
        missingIfNull(missing, "employees", employees);
        missingIfNull(missing, "foreign_trade", fts);
        missingIfNull(missing, "city", text(company, "city"));
        missingIfNull(missing, "company_age", text(company, "company_age_years_gradation"));
        missingIfNull(missing, "business_stage", text(company, "eco_mark_label"));
        missingIfNull(missing, "ownership", text(company, "okfs_name"));
        missingIfNull(missing, "region", text(company, "region"));
        String[] productNames = {"settlement", "guarantees", "loans", "leasing", "deposits", "payroll", "factoring"};
        Boolean[] productValues = {products.settlement(), products.guarantees(), products.loans(), products.leasing(),
                products.deposits(), products.payroll(), products.factoring()};
        for (int i = 0; i < productNames.length; i++) {
            missingIfNull(missing, "products." + productNames[i], productValues[i]);
        }
        if (flows.counterparties().isEmpty()) {
            missing.add("counterparties");
        }
        missingIfNull(missing, "self_transfer_share", flows.selfShare());
        Map<String, String> supplied = userProvided == null ? Map.of() : Map.copyOf(userProvided);
        var profile = new CompanyProfile(companyId, groupId, text(company, "mainokved_code"), text(company, "mainokved_name"),
                null, revenue, null, employees, text(company, "company_age_years_gradation"),
                text(company, "eco_mark_label"), text(company, "okfs_name"), text(company, "region"), text(company, "city"),
                text(company, "cl_type"), products, fts, strings(company.get("fts_countries")), flows.counterparties(),
                List.copyOf(memberOkveds), flows.selfShare(), supplied, List.copyOf(missing));
        boolean sufficient = sufficient(profile);
        ObjectNode provenance = provenance(metadata, company, group, links, fns, spark, employees, flows, supplied);
        return new ProfileSnapshot(profile, provenance, List.copyOf(issues), sufficient);
    }

    private void validateMetadata(Map<String, Object> metadata) {
        Object year = metadata.get("revenue_year");
        if (!"0.3.0".equals(text(metadata, "schema_version")) || !(year instanceof Number n) || n.intValue() != 2025
                || !"RUB".equals(text(metadata, "currency"))) {
            throw new ProfileDataException(ProfileDataException.Reason.DATASET_INCOMPATIBLE,
                    "Ожидается схема импорта 0.3.0, выручка в RUB за 2025 год");
        }
    }

    private void gate(Map<String, Object> company) {
        String status = text(company, "profile_status");
        if ("ambiguous".equals(status)) {
            throw new ProfileDataException(ProfileDataException.Reason.AMBIGUOUS_COMPANY,
                    "Идентификатор компании неоднозначен; исходная запись не выбирается");
        }
        if ("external".equals(status)) {
            throw new ProfileDataException(ProfileDataException.Reason.EXTERNAL_COMPANY,
                    "Для внешнего участника нет исходного профиля компании");
        }
        if (!"available".equals(status) || company.get("source_row") == null) {
            throw new ProfileDataException(ProfileDataException.Reason.DATASET_INCOMPATIBLE,
                    "Статус компании не согласован с исходным профилем");
        }
        if (!"CLIENT".equals(text(company, "cl_type"))) {
            throw new ProfileDataException(ProfileDataException.Reason.NOT_CLIENT,
                    "Персональная лента доступна действующим клиентам банка");
        }
    }

    private boolean sufficient(CompanyProfile p) {
        return p.okvedCode() != null || p.okvedName() != null || p.revenueRub() != null || p.employees() != null
                || p.companyAge() != null || p.businessStage() != null || p.ownership() != null
                || p.region() != null || p.city() != null || p.foreignTrade() != null
                || !p.foreignTradeCountries().isEmpty() || !p.counterparties().isEmpty() || !p.groupMemberOkveds().isEmpty()
                || p.selfTransferShare() != null || !p.userProvided().isEmpty()
                || p.products().settlement() != null || p.products().guarantees() != null || p.products().loans() != null
                || p.products().leasing() != null || p.products().deposits() != null || p.products().payroll() != null
                || p.products().factoring() != null;
    }

    private Flows flows(String companyId, String groupId, List<Map<String, Object>> rows, Set<String> issues) {
        Map<String, Bucket> buckets = new TreeMap<>();
        BigDecimal incoming = null, outgoing = null, self = null;
        BigInteger operations = BigInteger.ZERO;
        int eligibleCount = 0, excludedCount = 0, selfRowCount = 0;
        var allRows = mapper.createArrayNode();
        for (var row : rows) {
            String payer = text(row, "payer"), beneficiary = text(row, "beneficiary");
            boolean isSelf = companyId.equals(payer) && companyId.equals(beneficiary);
            if (isSelf) {
                selfRowCount++;
            }
            BigDecimal amount = decimal(row, "pay_amt");
            Object count = row.get("pay_cnt");
            boolean valid = payer != null && beneficiary != null && amount != null && amount.signum() >= 0
                    && count instanceof Number n && n.longValue() >= 0
                    && !"ambiguous".equals(text(row, "payer_profile_status"))
                    && !"ambiguous".equals(text(row, "beneficiary_profile_status"));
            boolean eligible = Boolean.TRUE.equals(row.get("ml_eligible")) && valid;
            ObjectNode source = mapper.valueToTree(row);
            source.put("effective_ml_eligible", eligible);
            source.put("self_transfer", isSelf);
            if (Boolean.TRUE.equals(row.get("ml_eligible")) && !valid) {
                source.put("backend_quality_issue", "invalid_eligible_payment_record");
                issues.add("invalid_eligible_payment_record");
            }
            allRows.add(source);
            if (!eligible) {
                excludedCount++;
                continue;
            }
            eligibleCount++;
            operations = operations.add(new BigInteger(count.toString()));
            if (isSelf) {
                self = add(self, amount);
                continue;
            }
            String direction = companyId.equals(beneficiary) ? "incoming" : "outgoing";
            if ("incoming".equals(direction)) {
                incoming = add(incoming, amount);
            } else {
                outgoing = add(outgoing, amount);
            }
            String counterpartyId = "incoming".equals(direction) ? payer : beneficiary;
            String key = counterpartyId + ":" + direction;
            Bucket bucket = buckets.computeIfAbsent(key, ignored -> new Bucket(counterpartyId, direction, row));
            bucket.amount = bucket.amount.add(amount);
            bucket.sourceRows.add(((Number) row.get("row_id")).longValue());
        }
        if (excludedCount > 0) {
            issues.add("excluded_payment_rows_preserved_outside_ml_features");
        }
        if (!rows.isEmpty()) {
            issues.add("payments_establish_financial_flows_not_supplier_customer_relationships");
        }
        List<Counterparty> counterparties = new ArrayList<>();
        var index = mapper.createArrayNode();
        for (Bucket bucket : buckets.values()) {
            BigDecimal denominator = "incoming".equals(bucket.direction) ? incoming : outgoing;
            String counterpartGroup = text(bucket.source, "counterparty_group_id");
            Boolean sameGroup = groupId == null || counterpartGroup == null ? null : groupId.equals(counterpartGroup);
            counterparties.add(new Counterparty(bucket.companyId, bucket.direction, bucket.amount,
                    ratio(bucket.amount, denominator), text(bucket.source, "counterparty_okved_code"),
                    text(bucket.source, "counterparty_okved_name"), text(bucket.source, "counterparty_region"), sameGroup));
            ObjectNode mapping = index.addObject();
            mapping.put("index", counterparties.size() - 1).put("companyId", bucket.companyId).put("direction", bucket.direction);
            mapping.set("paymentSourceRows", mapper.valueToTree(bucket.sourceRows));
            mapping.set("companySourceRow", mapper.valueToTree(bucket.source.get("counterparty_source_row")));
            mapping.set("companyQualityIssues", mapper.valueToTree(strings(bucket.source.get("counterparty_quality_issues"))));
            mapping.put("profileStatus", text(bucket.source, "incoming".equals(bucket.direction)
                    ? "payer_profile_status" : "beneficiary_profile_status"));
            mapping.put("denominatorRub", denominator);
            mapping.put("formula", "sum(eligible non-self amounts for counterparty and direction) / sum(eligible non-self amounts for direction)");
        }
        BigDecimal denominator = add(add(incoming, outgoing), self);
        BigDecimal selfShare = ratio(self == null ? BigDecimal.ZERO : self, denominator);
        ObjectNode evidence = mapper.createObjectNode();
        evidence.put("sourceFile", "df_Uid_payment_hack.csv").put("coverage", "unknown");
        evidence.put("observedRows", rows.size()).put("eligibleRows", eligibleCount).put("excludedRows", excludedCount);
        evidence.put("selfRowsIncludingExcluded", selfRowCount).put("eligibleOperations", eligibleCount == 0 ? null : operations);
        evidence.put("incomingNonSelfRub", incoming).put("outgoingNonSelfRub", outgoing).put("selfRub", self);
        evidence.put("selfShareDenominatorRub", denominator).put("selfShare", selfShare);
        evidence.put("selfShareFormula", "eligible self amount / (eligible incoming non-self + outgoing non-self + self amount counted once)");
        evidence.put("scope", "observed eligible rows of this snapshot; no claim about complete trade or payment activity");
        evidence.set("rows", allRows);
        evidence.set("counterpartyIndex", index);
        return new Flows(List.copyOf(counterparties), selfShare, evidence);
    }

    private ObjectNode provenance(Map<String, Object> metadata, Map<String, Object> company,
                                  List<Map<String, Object>> group, List<Map<String, Object>> links,
                                  String fns, String spark, String employees, Flows flows, Map<String, String> supplied) {
        ObjectNode result = mapper.createObjectNode();
        ObjectNode dataset = mapper.valueToTree(metadata);
        try {
            dataset.set("source_manifest", mapper.readTree(text(metadata, "source_manifest")));
        } catch (JsonProcessingException | IllegalArgumentException e) {
            throw new ProfileDataException(ProfileDataException.Reason.DATASET_INCOMPATIBLE,
                    "Метаданные происхождения набора повреждены");
        }
        dataset.put("importContractCommit", DATA_REVISION);
        result.set("dataset", dataset);
        ObjectNode original = mapper.createObjectNode();
        original.put("sourceFile", "df_Uid_data_hack.csv");
        original.set("attributes", mapper.valueToTree(company));
        original.put("revenueMeaning", "legal entity revenue in RUB for 2025; exactly 1000 is a placeholder");
        original.put("negativeMarkMeaning", "reliable information about key owner, not a negative risk flag");
        result.set("company", original);
        ObjectNode employeeSources = result.putObject("employees");
        employeeSources.put("fnsGradation", fns).put("sparkGradation", spark).put("mlValue", employees);
        employeeSources.put("resolution", fns != null && spark != null && !fns.equals(spark) ? "conflict_unresolved"
                : employees == null ? "unknown" : fns != null && spark != null ? "sources_agree" : "single_source");
        employeeSources.put("sourceFields", "AMOUNT_WORKERS_FNS_GRADATION, AMOUNT_WORKERS_SPARK_GRADATION");
        ObjectNode registration = result.putObject("registration");
        registration.put("city", text(company, "city")).put("region", text(company, "region"));
        registration.put("sourceFields", "CITY, REGION");
        ObjectNode servicing = result.putObject("servicing");
        servicing.put("sourceFile", "df_Fid_Uid_link_hack.csv");
        servicing.put("meaning", "company servicing geography; does not establish person residence, employment, ownership or role");
        servicing.set("links", mapper.valueToTree(links));
        ObjectNode groupEvidence = result.putObject("group");
        groupEvidence.put("groupId", text(company, "group_id")).put("compositionCoverage", "unknown");
        groupEvidence.putNull("consolidatedRevenueRub");
        groupEvidence.put("largeBusinessClassification", "unknown_without_consolidated_group_revenue");
        BigDecimal observedSum = null;
        int revenueCount = 0;
        for (var member : group) {
            BigDecimal revenue = observedRevenue(member);
            if (revenue != null) {
                observedSum = add(observedSum, revenue);
                revenueCount++;
            }
        }
        groupEvidence.put("availableMemberRevenueSumRub", observedSum).put("membersWithObservedRevenue", revenueCount);
        groupEvidence.put("sumMeaning", "sum of available observed legal-entity revenues; not consolidated group revenue");
        groupEvidence.set("availableMembers", mapper.valueToTree(group));
        result.set("payments", flows.evidence());
        ObjectNode foreign = result.putObject("foreignTrade");
        foreign.set("ftsFlag", mapper.valueToTree(company.get("fts_flag")));
        foreign.set("countries", mapper.valueToTree(strings(company.get("fts_countries"))));
        foreign.put("meaning", "absence of FTS information does not establish absence of trade; countries do not establish import/export direction");
        result.set("userProvided", mapper.valueToTree(supplied));
        result.put("roleOrigin", "external request; never inferred from person-company links");
        return result;
    }

    private static BigDecimal observedRevenue(Map<String, Object> company) {
        BigDecimal revenue = decimal(company, "revenue_company");
        return "observed".equals(text(company, "revenue_status")) && revenue != null && revenue.signum() >= 0
                && revenue.compareTo(PLACEHOLDER) != 0 ? revenue : null;
    }

    private static BigDecimal decimal(Map<String, Object> row, String field) {
        Object value = row.get(field);
        return value instanceof BigDecimal decimal ? decimal : value instanceof Number ? new BigDecimal(value.toString()) : null;
    }

    private static String text(Map<String, Object> row, String field) {
        Object value = row.get(field);
        return value == null || value.toString().isBlank() ? null : value.toString();
    }

    private static Boolean bool(Map<String, Object> row, String field) {
        return row.get(field) instanceof Boolean flag ? flag : null;
    }

    private static List<String> strings(Object value) {
        if (!(value instanceof List<?> list)) {
            return List.of();
        }
        return list.stream().filter(java.util.Objects::nonNull).map(Object::toString).toList();
    }

    private static BigDecimal add(BigDecimal left, BigDecimal right) {
        return left == null ? right : right == null ? left : left.add(right);
    }

    private static BigDecimal ratio(BigDecimal numerator, BigDecimal denominator) {
        return numerator == null || denominator == null || denominator.signum() <= 0 ? null
                : numerator.divide(denominator, MathContext.DECIMAL128);
    }

    private static void missingIfNull(Set<String> missing, String field, Object value) {
        if (value == null) {
            missing.add(field);
        }
    }

    private record Flows(List<Counterparty> counterparties, BigDecimal selfShare, ObjectNode evidence) {
    }

    private static final class Bucket {
        private final String companyId;
        private final String direction;
        private final Map<String, Object> source;
        private final List<Long> sourceRows = new ArrayList<>();
        private BigDecimal amount = BigDecimal.ZERO;

        private Bucket(String companyId, String direction, Map<String, Object> source) {
            this.companyId = companyId;
            this.direction = direction;
            this.source = source;
        }
    }
}
