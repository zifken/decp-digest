#!/bin/sh
# Weekly DECP digest: fetch -> build -> email. Monday 07:30 via crontab.
# On any failure (or empty week), email a notice so it never breaks silently.
cd "$(dirname "$0")" || exit 1
LOG=data/out/cron.log
TODAY=$(date +%F)
F="data/out/digest-$TODAY.md"

notify() { # notify <subject> <body>
    himalaya message send <<EOF
From: bioniclia@gmail.com
To: bioniclia@gmail.com
Subject: $1

$2
EOF
}

echo "=== $(date -Is) run" >> "$LOG"
if ! python3 fetch.py >> "$LOG" 2>&1; then
    notify "DECP digest — ERREUR fetch ($(date +%F))" "fetch.py a échoué. Log (30 dernières lignes) :
$(tail -30 "$LOG")"
    exit 1
fi
if ! python3 digest.py >> "$LOG" 2>&1; then
    notify "DECP digest — ERREUR digest ($(date +%F))" "digest.py a échoué. Log (30 dernières lignes) :
$(tail -30 "$LOG")"
    exit 1
fi

if [ -f "$F" ]; then
    python3 - "$F" <<'PY' >> "$LOG" 2>&1
import subprocess, sys
body = open(sys.argv[1]).read()
subprocess.run(['himalaya','message','send'], input=(
    "From: bioniclia@gmail.com\nTo: bioniclia@gmail.com\n"
    f"Subject: Digest DECP — semaine du {sys.argv[1].split('digest-')[1][:10]}\n\n"+body).encode(), check=True)
print("email sent")
PY
    if [ $? -ne 0 ]; then
        notify "DECP digest — ERREUR envoi email ($(date +%F))" "digest généré ($F) mais l'envoi himalaya a échoué. Digest sur disque : $F"
        exit 1
    fi
    echo "=== $(date -Is) done (digest sent)" >> "$LOG"
else
    notify "DECP digest — rien de nouveau ($(date +%F))" "Aucun nouveau avis depuis la dernière semaine (dédupe). Pas de digest généré — ce n'est pas une erreur."
    echo "=== $(date -Is) done (no new notices, notification sent)" >> "$LOG"
fi