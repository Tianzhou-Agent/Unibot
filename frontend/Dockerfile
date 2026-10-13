ARG VITE_BASE_PATH=/
FROM node:22-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG VITE_BASE_PATH
ARG VITE_API_BASE_URL=
ENV VITE_BASE_PATH=${VITE_BASE_PATH} \
    VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build


FROM nginx:1.27-alpine

ARG VITE_BASE_PATH
COPY nginx.conf /tmp/unibot-nginx.conf
RUN export UNIBOT_BASE_PATH="${VITE_BASE_PATH%/}/" \
    && export UNIBOT_BASE_PATH_NO_SLASH="${VITE_BASE_PATH%/}" \
    && envsubst '${UNIBOT_BASE_PATH} ${UNIBOT_BASE_PATH_NO_SLASH}' < /tmp/unibot-nginx.conf > /etc/nginx/conf.d/default.conf \
    && rm /tmp/unibot-nginx.conf
COPY --from=builder /app/dist /usr/share/nginx/html${VITE_BASE_PATH}

EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 \
    CMD wget -q -O - http://127.0.0.1/healthz | grep -q '^ok$'
