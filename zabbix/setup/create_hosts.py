#!/usr/bin/env python3
"""Create or update FreeIPA / Logstash hosts in Zabbix 8.0 from a CSV file.

For each line of the CSV the script makes sure the host exists, has an agent
interface on the given IP, belongs to the host group and is linked to the
templates of its role. Existing hosts keep their other templates, groups and
macros: the script only adds what is missing. It can be run again safely.

CSV format (header line required, ';' or ',' as separator):
    name;ip;role
    ipa01;10.0.0.11;freeipa
    logstash01;10.0.0.21;logstash

`name` must be identical to the Hostname set in zabbix_agent2.conf on the server
(setup_agent.sh prints it), otherwise active checks do not come up.

Usage:
    ZABBIX_API_TOKEN=<token> python3 create_hosts.py --url https://zabbix.example.org \\
        --group "Plateforme ELK" hosts.csv
    add --proxy "<proxy name>" if the hosts are monitored through a Zabbix proxy,
        --dry-run to only print what would be done.
"""
import argparse
import csv
import json
import os
import ssl
import sys
import urllib.request

# Templates linked for each role. Adapt the names to the templates you imported.
ROLE_TEMPLATES = {
    "freeipa": ["FreeIPA by Zabbix agent 2 active"],
    "logstash": ["SOC Logstash by Zabbix agent 2"],
}


class Api:
    def __init__(self, url, token, verify=True):
        self.url = url.rstrip("/") + "/api_jsonrpc.php"
        self.token = token
        self.ctx = None if verify else ssl._create_unverified_context()

    def call(self, method, params):
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
        req = urllib.request.Request(self.url, body, {
            "Content-Type": "application/json-rpc",
            "Authorization": "Bearer " + self.token,
        })
        with urllib.request.urlopen(req, timeout=60, context=self.ctx) as resp:
            data = json.load(resp)
        if "error" in data:
            sys.exit("Erreur de l'API Zabbix sur %s : %s" % (method, json.dumps(data["error"], ensure_ascii=False)))
        return data["result"]


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(2048)
        handle.seek(0)
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        rows = []
        for number, row in enumerate(csv.DictReader(handle, delimiter=delimiter), start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not any(row.values()) or row.get("name", "").startswith("#"):
                continue
            for column in ("name", "ip", "role"):
                if not row.get(column):
                    sys.exit("%s ligne %d : colonne « %s » vide ou absente" % (path, number, column))
            if row["role"] not in ROLE_TEMPLATES:
                sys.exit("%s ligne %d : rôle inconnu « %s » (attendu : %s)"
                         % (path, number, row["role"], ", ".join(sorted(ROLE_TEMPLATES))))
            rows.append(row)
    if not rows:
        sys.exit("%s : aucune ligne exploitable" % path)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", help="CSV file: name;ip;role")
    parser.add_argument("--url", required=True, help="Zabbix frontend URL")
    parser.add_argument("--group", required=True, help="Host group to put the hosts in (created if missing)")
    parser.add_argument("--proxy", help="Name of the Zabbix proxy monitoring these hosts (default: the server)")
    parser.add_argument("--dry-run", action="store_true", help="Print the actions without changing anything")
    parser.add_argument("--insecure", action="store_true", help="Do not verify the frontend TLS certificate")
    args = parser.parse_args()

    token = os.environ.get("ZABBIX_API_TOKEN")
    if not token:
        sys.exit("Définir la variable d'environnement ZABBIX_API_TOKEN avec un jeton d'API Zabbix.")
    api = Api(args.url, token, verify=not args.insecure)
    rows = read_csv(args.csv)

    # Templates: all must exist before anything is changed.
    wanted = sorted({name for row in rows for name in ROLE_TEMPLATES[row["role"]]})
    found = {t["host"]: t["templateid"] for t in api.call("template.get", {"filter": {"host": wanted}, "output": ["host"]})}
    missing = [name for name in wanted if name not in found]
    if missing:
        sys.exit("Modèles introuvables dans Zabbix (à importer d'abord) : %s" % ", ".join(missing))

    groups = api.call("hostgroup.get", {"filter": {"name": args.group}, "output": ["groupid"]})
    if groups:
        groupid = groups[0]["groupid"]
    elif args.dry_run:
        groupid = None
        print("[simulation] création du groupe « %s »" % args.group)
    else:
        groupid = api.call("hostgroup.create", {"name": args.group})["groupids"][0]
        print("groupe créé : %s" % args.group)

    proxy = {}
    if args.proxy:
        proxies = api.call("proxy.get", {"filter": {"name": args.proxy}, "output": ["proxyid"]})
        if not proxies:
            sys.exit("Proxy introuvable : %s" % args.proxy)
        proxy = {"monitored_by": 1, "proxyid": proxies[0]["proxyid"]}

    for row in rows:
        templateids = [found[name] for name in ROLE_TEMPLATES[row["role"]]]
        existing = api.call("host.get", {
            "filter": {"host": row["name"]}, "output": ["hostid"],
            "selectParentTemplates": ["templateid"], "selectHostGroups": ["groupid"],
            "selectInterfaces": ["interfaceid", "type", "ip"]})
        if not existing:
            if args.dry_run:
                print("[simulation] création de %s (%s, %s)" % (row["name"], row["ip"], row["role"]))
                continue
            params = {"host": row["name"], "groups": [{"groupid": groupid}],
                      "templates": [{"templateid": t} for t in templateids],
                      "interfaces": [{"type": 1, "main": 1, "useip": 1, "ip": row["ip"], "dns": "", "port": "10050"}]}
            params.update(proxy)
            api.call("host.create", params)
            print("créé       : %s (%s, %s)" % (row["name"], row["ip"], row["role"]))
            continue

        host = existing[0]
        changes = []
        current_templates = [t["templateid"] for t in host["parentTemplates"]]
        new_templates = [t for t in templateids if t not in current_templates]
        current_groups = [g["groupid"] for g in host["hostgroups"]]
        update = {"hostid": host["hostid"]}
        if new_templates:
            update["templates"] = [{"templateid": t} for t in current_templates + new_templates]
            changes.append("%d modèle(s) ajouté(s)" % len(new_templates))
        if groupid and groupid not in current_groups:
            update["groups"] = [{"groupid": g} for g in current_groups + [groupid]]
            changes.append("ajouté au groupe")
        agent_interfaces = [i for i in host["interfaces"] if i["type"] == "1"]
        add_interface = not agent_interfaces
        if agent_interfaces and all(i["ip"] != row["ip"] for i in agent_interfaces):
            print("attention  : %s a déjà une interface agent sur %s (CSV : %s), non modifiée"
                  % (row["name"], agent_interfaces[0]["ip"], row["ip"]))
        if add_interface:
            changes.append("interface agent ajoutée")
        if not changes:
            print("déjà à jour : %s" % row["name"])
            continue
        if args.dry_run:
            print("[simulation] %s : %s" % (row["name"], ", ".join(changes)))
            continue
        if add_interface:
            api.call("hostinterface.create", {"hostid": host["hostid"], "type": 1, "main": 1, "useip": 1,
                                              "ip": row["ip"], "dns": "", "port": "10050"})
        if len(update) > 1:
            api.call("host.update", update)
        print("mis à jour : %s (%s)" % (row["name"], ", ".join(changes)))


if __name__ == "__main__":
    main()
