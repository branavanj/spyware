#!/bin/bash
# Prepare a server for Zabbix monitoring: Zabbix agent 2 configuration + role prerequisites.
# Run as root ON the server to monitor (FreeIPA or Logstash). Safe to run again.
#
# Usage:
#   setup_agent.sh --role freeipa  --server <zabbix server or proxy IP> [--hostname <name in Zabbix>]
#   setup_agent.sh --role logstash --server <zabbix server or proxy IP> [--hostname <name in Zabbix>]
#
# Options:
#   --role       freeipa | logstash
#   --server     IP of the Zabbix server (or proxy) that monitors this machine
#   --hostname   host name as declared in Zabbix (default: short hostname of this machine)
#   --no-install do not try to install zabbix-agent2 if it is missing
#   --no-firewall do not touch firewalld
#
# What it does:
#   all roles : sets Server / ServerActive / Hostname in zabbix_agent2.conf (backup kept),
#               opens TCP 10050 for the Zabbix server in firewalld, restarts the agent.
#   freeipa   : /var/lib/zabbix/ipa + cron running ipa-healthcheck every 15 min (JSON readable by zabbix).
#   logstash  : checks that the Logstash monitoring API answers locally (nothing to install).

set -u
ROLE="" SERVER="" NAME="" INSTALL=1 FIREWALL=1
CONF="${ZABBIX_AGENT2_CONF:-/etc/zabbix/zabbix_agent2.conf}"
IPA_DIR=/var/lib/zabbix/ipa
IPA_JSON=$IPA_DIR/healthcheck.json
IPA_CRON=/etc/cron.d/zabbix-ipa-healthcheck

die()  { echo "ERREUR : $*" >&2; exit 1; }
info() { echo "[ok] $*"; }
warn() { echo "[!!] $*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --role) ROLE="${2:-}"; shift 2 ;;
        --server) SERVER="${2:-}"; shift 2 ;;
        --hostname) NAME="${2:-}"; shift 2 ;;
        --no-install) INSTALL=0; shift ;;
        --no-firewall) FIREWALL=0; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) die "option inconnue : $1" ;;
    esac
done
[ "$(id -u)" -eq 0 ] || die "à lancer en root"
case "$ROLE" in freeipa|logstash) ;; *) die "--role freeipa|logstash obligatoire" ;; esac
[ -n "$SERVER" ] || die "--server <IP du serveur ou proxy Zabbix> obligatoire"
[ -n "$NAME" ] || NAME="$(hostname -s)"

# ---------------------------------------------------------------- agent
if ! command -v zabbix_agent2 >/dev/null 2>&1; then
    [ "$INSTALL" -eq 1 ] || die "zabbix-agent2 n'est pas installé"
    command -v dnf >/dev/null 2>&1 || die "zabbix-agent2 absent et dnf introuvable"
    dnf -y install zabbix-agent2 || die "installation de zabbix-agent2 impossible (dépôt Zabbix configuré ?)"
fi
[ -f "$CONF" ] || die "fichier introuvable : $CONF"
id zabbix >/dev/null 2>&1 || die "l'utilisateur zabbix n'existe pas"

# Set "Key=value" in the agent configuration: replace the active line, or append it.
set_param() {
    local key="$1" value="$2"
    if grep -qE "^[[:space:]]*${key}=" "$CONF"; then
        sed -i -E "0,/^[[:space:]]*${key}=.*/s||${key}=${value}|" "$CONF"
        # drop any further active duplicate of the same key
        awk -v k="$key" 'BEGIN{n=0} { if ($0 ~ "^[[:space:]]*" k "=") { n++; if (n>1) next } print }' "$CONF" > "$CONF.tmp" && cat "$CONF.tmp" > "$CONF" && rm -f "$CONF.tmp"
    else
        printf '%s=%s\n' "$key" "$value" >> "$CONF"
    fi
}
[ -f "$CONF.orig" ] || cp -p "$CONF" "$CONF.orig"
set_param Server "$SERVER"
set_param ServerActive "$SERVER"
set_param Hostname "$NAME"
info "agent configuré : Server=$SERVER ServerActive=$SERVER Hostname=$NAME (sauvegarde : $CONF.orig)"

# ---------------------------------------------------------------- role: freeipa
if [ "$ROLE" = freeipa ]; then
    if ! command -v ipa-healthcheck >/dev/null 2>&1; then
        if [ "$INSTALL" -eq 1 ] && command -v dnf >/dev/null 2>&1; then
            dnf -y install ipa-healthcheck || warn "installation de ipa-healthcheck impossible"
        fi
    fi
    mkdir -p "$IPA_DIR" && chown root:zabbix "$IPA_DIR" && chmod 750 "$IPA_DIR"
    HC="$(command -v ipa-healthcheck || echo /usr/bin/ipa-healthcheck)"
    RUN="$HC --output-type json --output-file $IPA_JSON.tmp; chgrp zabbix $IPA_JSON.tmp; chmod 640 $IPA_JSON.tmp; mv -f $IPA_JSON.tmp $IPA_JSON"
    if [ -d /etc/cron.d ] && printf '# Rapport ipa-healthcheck pour Zabbix (géré par setup_agent.sh)\n*/15 * * * * root %s\n' "$RUN" > "$IPA_CRON"; then
        chmod 644 "$IPA_CRON"
        info "cron installé : $IPA_CRON"
    else
        warn "/etc/cron.d absent ou non inscriptible : la tâche cron n'a PAS été installée (paquet cronie ?)"
    fi
    if [ -x "$HC" ]; then
        sh -c "$RUN" >/dev/null 2>&1
        if [ -s "$IPA_JSON" ]; then info "premier rapport produit : $IPA_JSON"; else warn "ipa-healthcheck n'a pas produit $IPA_JSON"; fi
        if command -v runuser >/dev/null 2>&1 && ! runuser -u zabbix -- head -c 1 "$IPA_JSON" >/dev/null 2>&1; then
            warn "l'utilisateur zabbix ne peut pas lire $IPA_JSON"
        fi
    else
        warn "ipa-healthcheck introuvable : le rapport sera produit quand le paquet sera installé"
    fi
fi

# ---------------------------------------------------------------- role: logstash
if [ "$ROLE" = logstash ]; then
    if command -v curl >/dev/null 2>&1; then
        if curl -s -m 5 http://127.0.0.1:9600/ | grep -q '"version"'; then
            info "API de monitoring Logstash joignable sur 127.0.0.1:9600"
        else
            warn "l'API Logstash ne répond pas sur 127.0.0.1:9600 (Logstash arrêté, autre port, ou API protégée ?)"
        fi
    fi
fi

# ---------------------------------------------------------------- firewall + restart
if [ "$FIREWALL" -eq 1 ] && command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    RULE="rule family=ipv4 source address=$SERVER port port=10050 protocol=tcp accept"
    firewall-cmd -q --permanent --query-rich-rule="$RULE" || firewall-cmd -q --permanent --add-rich-rule="$RULE"
    firewall-cmd -q --reload && info "firewalld : TCP 10050 ouvert pour $SERVER"
fi
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files zabbix-agent2.service >/dev/null 2>&1; then
    systemctl enable -q zabbix-agent2 2>/dev/null
    if systemctl restart zabbix-agent2; then info "zabbix-agent2 redémarré"; else warn "échec du redémarrage de zabbix-agent2"; fi
else
    warn "systemd absent : redémarrer zabbix-agent2 à la main"
fi

echo
echo "Terminé. Nom à utiliser pour l'hôte dans Zabbix : $NAME"
