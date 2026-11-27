FROM node:22.23.5-slim@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb AS build

WORKDIR /app
RUN chown node:node /app
USER node

# Dépendances (cache des couches)
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci

# Code + build (output "standalone" : serveur minimal + node_modules utiles seulement ;
# messages i18n et polices next/font sont embarqués dans .next au build)
COPY --chown=node:node . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Image d'exécution : sans sources, devDependencies ni cache npm
FROM node:22.23.5-slim@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
# (pas de dossier public/ : s'il est ajouté, le copier aussi vers ./public)
USER node

EXPOSE 3000
CMD ["node", "server.js"]
