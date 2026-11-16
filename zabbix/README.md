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
