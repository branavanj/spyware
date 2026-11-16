# Templates Zabbix 8.0

Import : *Data collection → Templates → Import*, format YAML. Les templates arrivent dans le groupe `Templates/SOC`.

## `templates/soc_web_access_by_http.yaml` — SOC - Accès web par HTTP

Contrôle générique d'une URL par scénario web, exécuté par le serveur ou le proxy Zabbix (aucun agent).
Un hôte = une URL.

| Macro | Défaut | Rôle |
|---|---|---|
| `{$WEB.URL}` | *(vide, obligatoire)* | URL complète à contrôler |
| `{$WEB.REQUIRED.STRING}` | *(vide)* | Texte attendu dans la page (regex) |
| `{$WEB.STATUS.CODES}` | `200` | Codes HTTP acceptés |
| `{$WEB.RESPONSE.TIME.WARN}` | `3` | Seuil de lenteur, en secondes |
| `{$WEB.TIMEOUT}` | `15s` | Délai maximal de réponse |
| `{$WEB.INTERVAL}` | `1m` | Fréquence du contrôle |

Alertes : accès indisponible (3 échecs consécutifs, HIGH), temps de réponse élevé (5 min, WARNING).

Le certificat est vérifié : une AC interne doit être connue du serveur/proxy Zabbix (`SSLCALocation`).

## `templates/soc_freeipa_by_zabbix_agent2.yaml` — SOC - FreeIPA par Zabbix agent 2

À lier à chaque serveur FreeIPA (Zabbix agent 2 requis sur le serveur).

- Services systemd FreeIPA, par découverte (`{$IPA.SERVICES.MATCHES}`).
- Ports locaux 389, 636, 88, 464, 443.
- Certificat HTTPS : validité et expiration (`{$IPA.FQDN}` obligatoire, `{$IPA.CERT.EXPIRY.WARN}` = 21 jours).
- Rapport `ipa-healthcheck` : nombre d'erreurs, d'avertissements, détail, fraîcheur du rapport.

Prérequis pour `ipa-healthcheck` sur le serveur FreeIPA :

```bash
dnf install ipa-healthcheck
systemctl enable --now ipa-healthcheck.timer
# donner la lecture du rapport à l'utilisateur zabbix
setfacl -m u:zabbix:x /var/log/ipa /var/log/ipa/healthcheck
setfacl -m u:zabbix:r /var/log/ipa/healthcheck/healthcheck.log
```

Pour l'interface web FreeIPA, lier aussi « SOC - Accès web par HTTP » avec `{$WEB.URL}` = `https://<fqdn>/ipa/ui/`.

## Elasticsearch (testé sur Elasticsearch 9.2.2)

Trois templates complémentaires, sans métrique ni alerte en commun :

| Template | À lier à | Rôle |
|---|---|---|
| `Elasticsearch Cluster by HTTP` (officiel Zabbix) | un seul hôte « cluster » | Santé green/yellow/red, shards, heap, latences et pools de threads par nœud |
| `templates/soc_elasticsearch_health_report_by_http.yaml` | le même hôte « cluster » | Indicateurs `_health_report` (master stable, ILM, SLM, dépôts de snapshots, capacité en shards), licence, master élu |
| `templates/soc_elasticsearch_node_by_http.yaml` | chaque serveur Elasticsearch | Statut du serveur, rôles, version, circuit breakers |

Macros de connexion communes aux trois (à poser en macros globales) : `{$ELASTICSEARCH.USERNAME}`,
`{$ELASTICSEARCH.PASSWORD}`, `{$ELASTICSEARCH.SCHEME}`, `{$ELASTICSEARCH.PORT}`.
Sur l'hôte « cluster » : `{$ELASTICSEARCH.HOST}`.

Statut par serveur (`ES: Node status`) : `0` Unreachable, `1` Out of cluster, `2` OK.

Alertes du template par serveur : nœud injoignable, nœud hors cluster (après `{$ELASTICSEARCH.NODE.STATUS.DELAY}` = 3m).
CPU, mémoire et disques restent supervisés par le template Linux (Zabbix agent).

Alertes du rapport de santé : une par indicateur, YELLOW ou RED, après `{$ELASTICSEARCH.HEALTH.DELAY}` = 10m.
`shards_availability` et `disk` sont exclus par défaut (`{$ELASTICSEARCH.HEALTH.INDICATOR.NOT_MATCHES}`) car déjà couverts ailleurs.

## `templates/soc_logstash_by_zabbix_agent2.yaml` — SOC - Logstash par Zabbix agent 2 (testé sur Logstash 9.2.2)

À lier à chaque serveur Logstash. L'API de monitoring (port 9600) est interrogée en local par Zabbix agent 2 :
aucun flux à ouvrir, l'API peut rester sur `127.0.0.1`. Prérequis : API sans authentification ni TLS (défaut Logstash).

- Joignabilité, statut green/yellow/red calculé par Logstash, version, uptime, heap JVM.
- Événements reçus et émis par seconde, événements en file, contre-pression.
- Par pipeline (découverte) : débits, charge des workers, remplissage de la file persistante, dernier rechargement.

Alertes : Logstash injoignable (3m), statut RED (5m), statut YELLOW (30m), heap > 85 % sur 1 h,
file persistante > 80 % sur 10 min, dernier rechargement de configuration en échec.
Si l'agent Zabbix est arrêté, ce template ne lève rien : seule l'alerte agent part.

## `templates/soc_kibana_by_http.yaml` — SOC - Kibana par HTTP (testé sur Kibana 9.2.2)

À lier à chaque serveur Kibana. Interroge `/api/status` depuis le serveur/proxy Zabbix.

- Sans authentification : joignabilité et statut (available / degraded / unavailable / critical).
- Avec `{$KIBANA.AUTH.HEADER}` (`ApiKey <clé>` ou `Basic <base64>`) : explication du statut, version,
  temps de réponse, connexions simultanées, mémoire heap.

Alertes : Kibana injoignable (3m), statut unavailable/critical (5m), statut degraded (30m).

## Kafka

Pas de template maison : le template officiel `Apache Kafka by JMX` fonctionne sur Kafka 4.1 en KRaft (testé).
Prérequis : JMX activé sur chaque broker et une Zabbix Java gateway joignable par le serveur.
Les items ZooKeeper du template restent « non supportés » en KRaft, sans lever d'alerte.

## `dashboards/create_elk_dashboard.py` — dashboard « SOC - Plateforme ELK »

Les dashboards globaux ne s'importent pas en YAML : ce script le crée (ou le met à jour) par l'API.
Il suffit d'un groupe d'hôtes contenant tous les hôtes de la plateforme ; les widgets sélectionnent par nom d'item.

```bash
ZABBIX_API_TOKEN=<jeton API> python3 dashboards/create_elk_dashboard.py \
    --url https://<zabbix> --group "<groupe d'hôtes ELK>"
```

Pages : Vue d'ensemble (une case colorée par serveur Elasticsearch / Logstash / Kibana, partitions Kafka,
indicateurs de santé, problèmes en cours), Elasticsearch, Logstash, Kafka, Kibana.
Les graphiques sélectionnent les items par nom sur tous les hôtes, pas seulement ceux du groupe.

## `templates/soc_qradar_by_http.yaml` — SOC - QRadar par HTTP

À lier à un seul hôte : la Console QRadar. Découvre toutes les machines du déploiement via
`/api/config/deployment/hosts` (jeton « Authorized Service » en lecture seule dans `{$QRADAR.API.TOKEN}`).

- Statut de chaque machine ; alerte si différent de `{$QRADAR.HOST.STATUS.OK}` (`Active` ou `Standby`).
- EPS moyen, de pointe et alloué ; alerte si l'EPS moyen dépasse 90 % de l'allocation pendant 15 min.
- Alerte si l'API de la Console ne répond plus depuis 15 min.

Validé uniquement contre une API simulée : vérifier les noms de champs sur la vraie Console avec

```bash
curl -k -H 'SEC: <jeton>' -H 'Accept: application/json' https://<console>/api/config/deployment/hosts
```
