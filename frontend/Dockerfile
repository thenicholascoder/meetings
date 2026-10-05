FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_LK_TOKEN_ENDPOINT=/api/getToken
ARG NEXT_PUBLIC_LK_RECORD_ENDPOINT=/api/record
ARG NEXT_PUBLIC_SHOW_SETTINGS_MENU=true
ENV NEXT_PUBLIC_LK_TOKEN_ENDPOINT=$NEXT_PUBLIC_LK_TOKEN_ENDPOINT \
    NEXT_PUBLIC_LK_RECORD_ENDPOINT=$NEXT_PUBLIC_LK_RECORD_ENDPOINT \
    NEXT_PUBLIC_SHOW_SETTINGS_MENU=$NEXT_PUBLIC_SHOW_SETTINGS_MENU \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
