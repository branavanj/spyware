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
| `templates/soc_elasticsearch_node_by_http.yaml` | chaque serveur Elasticsearch | Statut du serveur, rôles, version, CPU, circuit breakers, disque libre en % |

Macros de connexion communes aux trois (à poser en macros globales) : `{$ELASTICSEARCH.USERNAME}`,
`{$ELASTICSEARCH.PASSWORD}`, `{$ELASTICSEARCH.SCHEME}`, `{$ELASTICSEARCH.PORT}`.
Sur l'hôte « cluster » : `{$ELASTICSEARCH.HOST}`.

Statut par serveur (`ES: Node status`) : `0` Unreachable, `1` Out of cluster, `2` OK.

Alertes du template par serveur : nœud injoignable, nœud hors cluster (après `{$ELASTICSEARCH.NODE.STATUS.DELAY}` = 3m),
disque libre < 15 % (WARNING) et < 10 % (HIGH).

Alertes du rapport de santé : une par indicateur, YELLOW ou RED, après `{$ELASTICSEARCH.HEALTH.DELAY}` = 10m.
`shards_availability` et `disk` sont exclus par défaut (`{$ELASTICSEARCH.HEALTH.INDICATOR.NOT_MATCHES}`) car déjà couverts ailleurs.

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
