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

## Chaîne de logs Kafka → Logstash → Elasticsearch

Trois points de mesure, un par maillon, plus la page « Chaîne de logs » du dashboard :

| Maillon | Template | À lier à | Ce qu'il dit |
|---|---|---|---|
| 1. Kafka | `templates/soc_kafka_consumer_lag_by_zabbix_agent2.yaml` | un seul broker | Messages en attente et consommateurs actifs, par groupe et topic |
| 2. Logstash | `templates/soc_logstash_by_zabbix_agent2.yaml` | chaque Logstash | Débit par pipeline, documents livrés / rejetés par sortie elasticsearch, envois en échec |
| 3. Elasticsearch | `templates/soc_elasticsearch_data_freshness_by_http.yaml` | l'hôte « cluster » | Âge du dernier document et volume sur 5 min, par motif d'index |

Alertes de la chaîne :

| Alerte | Sévérité | Signification |
|---|---|---|
| Kafka lag : No active consumer | HIGH | Plus personne ne lit le topic pour ce groupe (10 min) |
| Kafka lag : Consumer lag is high | WARNING | Retard > `{$KAFKA.LAG.MAX.WARN}` messages pendant 15 min |
| Logstash : Cannot deliver to Elasticsearch | AVERAGE | Envois bulk en échec pendant 5 min (une alerte par serveur Logstash) |
| Logstash pipeline : Pipeline is stalled | AVERAGE | Le pipeline reçoit sans rien émettre depuis 10 min |
| Logstash output : Documents are rejected | WARNING | Elasticsearch refuse des documents en continu depuis 10 min |
| Log freshness : No recent document | HIGH | Aucun document récent pour ce motif d'index : la chaîne est coupée quelque part |

Mise en place :

1. Sur un broker Kafka : copier `agent/kafka_consumer_lag.sh` dans `/etc/zabbix/scripts/` (exécutable) et
   `agent/kafka_consumer_lag.conf` dans `/etc/zabbix/zabbix_agent2.d/`, puis redémarrer zabbix-agent2.
   Si Kafka n'est pas dans `/opt/kafka/bin`, définir `KAFKA_BIN` dans le script. Cluster SASL/TLS : fichier de
   propriétés client dans `{$KAFKA.LAG.COMMAND.CONFIG}`.
2. Sur l'hôte « cluster » : renseigner `{$ELASTICSEARCH.FRESHNESS.PATTERNS}` (ex. `logs-*,winlogbeat-*`).
   Seuil par motif avec une macro à contexte, ex. `{$ELASTICSEARCH.FRESHNESS.MAX.AGE:"audit-*"}` = `2h`.
3. Limiter les groupes suivis avec `{$KAFKA.LAG.GROUP.MATCHES}` ; seuil par groupe avec `{$KAFKA.LAG.MAX.WARN:"<groupe>"}`.

Une panne franche lève une alerte par maillon touché (par exemple Logstash arrêté : Logstash injoignable,
plus de consommateur Kafka, plus de document récent). Zabbix ne permet pas de les fusionner depuis un template.

Après une mise à jour d'un template, les nouveaux items découverts apparaissent au plus tard une heure après.

## `templates/soc_logstash_by_zabbix_agent2.yaml` — SOC - Logstash par Zabbix agent 2 (testé sur Logstash 9.2.2)

À lier à chaque serveur Logstash. L'API de monitoring (port 9600) est interrogée en local par Zabbix agent 2 :
aucun flux à ouvrir, l'API peut rester sur `127.0.0.1`. Prérequis : API sans authentification ni TLS (défaut Logstash).

- Joignabilité, statut green/yellow/red calculé par Logstash, version, uptime, heap JVM.
- Événements reçus et émis par seconde, événements en file, contre-pression.
- Par pipeline (découverte) : débits, charge des workers, remplissage de la file persistante, dernier rechargement.
- Par sortie elasticsearch (découverte) : documents livrés par seconde, documents rejetés, envois en échec, réponses 429.

Alertes : Logstash injoignable (3m), statut RED (5m), statut YELLOW (30m), heap > 85 % sur 1 h,
file persistante > 80 % sur 10 min, dernier rechargement de configuration en échec,
plus les trois alertes Logstash de la chaîne de logs ci-dessus.
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

Pages : Vue globale (sur un écran : problèmes par sévérité, une case colorée par serveur Elasticsearch / Logstash / Kibana,
Kafka, chaîne de logs, CPU, mémoire, bande passante, problèmes en cours), Chaîne de logs, Elasticsearch, Logstash, Kafka, Kibana,
Système (bande passante par interface, CPU, mémoire, disques, charge, erreurs réseau ; items du template officiel `Linux by Zabbix agent`).
Les graphiques listent explicitement les hôtes du groupe : relancer le script après ajout ou retrait d'un hôte.


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

## `grafana/soc_plateforme_elk.json` — le même dashboard pour Grafana

À importer dans Grafana (*Dashboards → New → Import*). Prérequis : plugin `alexanderzobnin-zabbix-app` installé et activé,
avec une source de données Zabbix pointant sur `https://<zabbix>/api_jsonrpc.php`.

Deux variables en haut du dashboard : la source Zabbix et le groupe d'hôtes de la plateforme.
En haut, la « Vue globale » (mêmes informations que la première page du dashboard Zabbix), toujours visible.
En dessous, des sections de détail repliées : Chaîne de logs, Système, Elasticsearch, Logstash, Kafka, Kibana.
Requiert Grafana 11.6 ou plus récent avec le plugin Zabbix 6.x.

Testé sur Grafana 12.2.0 avec le plugin Zabbix 6.10.3 contre Zabbix 8.0.0 : la vue globale s'affiche avec des données réelles.
Les sections de détail repliées n'ont pas été ouvertes pendant le test.
