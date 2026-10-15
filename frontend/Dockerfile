FROM node:22-slim

WORKDIR /app

# Dépendances (cache des couches)
COPY package.json package-lock.json ./
RUN npm ci

# Code + build
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
