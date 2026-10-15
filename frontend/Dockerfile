FROM node:22-slim

WORKDIR /app
RUN chown node:node /app
USER node

# Dépendances (cache des couches)
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci

# Code + build
COPY --chown=node:node . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
