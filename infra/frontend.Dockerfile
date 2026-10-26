FROM nginx:1.28-alpine
COPY frontend/web /usr/share/nginx/html
COPY infra/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY infra/nginx/config.js /usr/share/nginx/html/config.js
COPY infra/nginx/15-check-token.sh /docker-entrypoint.d/15-check-token.sh
RUN chmod +x /docker-entrypoint.d/15-check-token.sh
EXPOSE 80
