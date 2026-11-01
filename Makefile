.PHONY: up down build logs ps

# Start the stack (build + up)
up:
	docker compose up -d --build

# Stop the stack
down:
	docker compose down

# Build the images
build:
	docker compose build

# Live logs
logs:
	docker compose logs -f

# Service status
ps:
	docker compose ps
