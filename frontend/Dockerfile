FROM node:22-alpine AS builder
WORKDIR /app
# .npmrc muss vor dem Install da sein: @meshtastic/* liegt in der
# JSR-Registry. Ohne die Datei klappt es nur, solange die Lockfile die
# URLs schon aufgeloest hat - ein frischer Install schluege fehl.
COPY package.json package-lock.json* .npmrc ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:stable-alpine
RUN apk add --no-cache openssl
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site.conf  /etc/nginx/site.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
# CRLF -> LF, sonst bricht der Shebang unter Linux
RUN sed -i 's/\r//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh
EXPOSE 80 443
ENTRYPOINT ["/docker-entrypoint.sh"]
