#!/bin/sh
# Restaure une sauvegarde (scripts/backup.sh) : REMPLACE toute la base courante.
#
# Usage :  scripts/restore.sh backups/diggypaper-AAAAMMJJ-HHMMSS.sql.gz
# Pour vérifier une sauvegarde sans toucher à la base en service :
#   TARGET_DB=essai scripts/restore.sh <fichier>   (base « essai » créée à côté, à supprimer ensuite)
set -eu
cd "$(dirname "$0")/.."

FILE="${1:?usage: scripts/restore.sh <sauvegarde.sql.gz>}"
[ -f "$FILE" ] || { echo "Fichier introuvable : $FILE" >&2; exit 1; }
gzip -t "$FILE"

PROD_DB="$(docker compose exec -T postgres sh -c 'printf %s "$POSTGRES_DB"')"
if [ -n "${TARGET_DB:-}" ] && [ "$TARGET_DB" = "$PROD_DB" ]; then
    echo "TARGET_DB est la base en service ($PROD_DB) : lancez le script sans TARGET_DB (confirmation et arrêt des services)." >&2
    exit 1
fi

if [ -z "${TARGET_DB:-}" ]; then
    printf "Remplacer TOUTE la base courante par %s ? (oui/non) " "$FILE"
    read -r ans
    [ "$ans" = "oui" ] || { echo "Annulé."; exit 1; }
    docker compose stop nginx frontend backend worker
fi

PSQL='psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER"'
DB_EXPR="${TARGET_DB:-\$POSTGRES_DB}"
docker compose exec -T postgres sh -c "$PSQL -d postgres -c \"DROP DATABASE IF EXISTS \\\"$DB_EXPR\\\" WITH (FORCE)\" -c \"CREATE DATABASE \\\"$DB_EXPR\\\" OWNER \\\"\$POSTGRES_USER\\\"\""
gzip -dc "$FILE" | docker compose exec -T postgres sh -c "$PSQL -q -d \"$DB_EXPR\" -o /dev/null"

if [ -z "${TARGET_DB:-}" ]; then
    docker compose up -d
    echo "Restauration terminée. Les migrations manquantes (si la sauvegarde est plus ancienne que le code) sont appliquées au démarrage du backend."
else
    echo "Restauré dans la base « $TARGET_DB » (vérification) ; la supprimer : docker compose exec postgres sh -c 'dropdb -U \"\$POSTGRES_USER\" $TARGET_DB'"
fi
