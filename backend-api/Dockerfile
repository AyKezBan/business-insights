FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN mvn -B -ntp dependency:go-offline
COPY src ./src
RUN mvn -B -ntp package

FROM eclipse-temurin:21-jre-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home backend
WORKDIR /app
COPY --from=build /src/target/backend-api-0.1.0.jar app.jar
USER backend
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
