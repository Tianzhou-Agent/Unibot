FROM alpine:3.20 AS assets
RUN apk add --no-cache ca-certificates wget unzip
RUN wget -q -O /tmp/draw.war https://github.com/jgraph/drawio/releases/download/v32.5.5/draw.war \
    && echo 'cde0ebd72da197da37d100353e226d5344b08f0fcf7e1b18d1e000a77f9c5f52  /tmp/draw.war' | sha256sum -c - \
    && unzip -q /tmp/draw.war -d /webapp \
    && rm -rf /webapp/WEB-INF /webapp/META-INF

FROM node:24-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --from=assets /webapp /data/assets/webapp
USER node
EXPOSE 3000 3333
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
    CMD node -e "fetch('http://127.0.0.1:3000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "node_modules/drawio-mcp-server/build/index.js", "--editor", "--transport", "http", "--host", "0.0.0.0", "--asset-path", "/data/assets"]
