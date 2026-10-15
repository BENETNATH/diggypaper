#!/bin/sh
# Sauvegarde de la base Diggypaper (comptes, projets, articles, PDF, annotations, modèles
# et clés chiffrées) : un pg_dump compressé par exécution, dans ./backups (BACKUP_DIR).
# Les zips d'export, poids de modèles (ollama, laya) et encodeurs sont reproductibles : non
# sauvegardés. ⚠ Garder aussi .env (ENCRYPTION_KEY : sans elle, les clés API chiffrées de la
# base sont illisibles) et copier ./backups HORS de cette machine.
#
# Usage (depuis la racine du dépôt) :  scripts/backup.sh
# Planifier (cron, tous les jours à 2 h) :
#   0 2 * * * cd /chemin/diggypaper && scripts/backup.sh >> backups/backup.log 2>&1
set -eu
cd "$(dirname "$0")/.."

DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$DIR"
OUT="$DIR/diggypaper-$(date +%Y%m%d-%H%M%S).sql.gz"

# pipefail absent de sh : on écrit d'abord dans un fichier temporaire, puis on renomme.
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" --no-owner --clean --if-exists "$POSTGRES_DB"' \
    | gzip > "$OUT.part"
if [ "$(gzip -dc "$OUT.part" | tail -n 5 | grep -c 'PostgreSQL database dump complete')" != "1" ]; then
    rm -f "$OUT.part"
    echo "ECHEC : dump incomplet" >&2
    exit 1
fi
mv "$OUT.part" "$OUT"
find "$DIR" -name 'diggypaper-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "OK : $OUT ($(du -h "$OUT" | cut -f1)), sauvegardes > ${KEEP_DAYS} j supprimées"
