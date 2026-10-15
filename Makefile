.PHONY: up down build logs ps

# Démarrer la stack (build + up)
up:
	docker compose up -d --build

# Arrêter la stack
down:
	docker compose down

# Builder les images
build:
	docker compose build

# Logs en temps réel
logs:
	docker compose logs -f

# État des services
ps:
	docker compose ps
