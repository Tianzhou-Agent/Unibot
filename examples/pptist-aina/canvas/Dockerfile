# PPTist (AGPL-3.0) at a pinned commit with the Superbot bridge, served by nginx, which also proxies /mcp to the
# tool server. Build context: this folder.
FROM node:22-alpine AS build

ARG PPTIST_REPO=https://github.com/pipipi-pikachu/PPTist.git
ARG PPTIST_COMMIT=04e06cb397b7f146c6e3e4f62ccddc0b72c041d2

RUN apk add --no-cache git
WORKDIR /pptist
RUN git init -q \
    && git remote add origin "$PPTIST_REPO" \
    && git fetch -q --depth 1 origin "$PPTIST_COMMIT" \
    && git checkout -q FETCH_HEAD
# --ignore-scripts skips PPTist's husky git hooks; nothing else in its tree needs install scripts.
RUN npm ci --ignore-scripts --no-audit --no-fund

COPY prepare.sh /aina/prepare.sh
COPY bridge /aina/bridge
# `npm run build` type-checks the bridge against this PPTist commit before bundling it.
RUN sh /aina/prepare.sh /pptist && npm run build


FROM nginx:1.27-alpine

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /pptist/dist /usr/share/nginx/html
