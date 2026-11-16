#!/usr/bin/env python3
"""Create or update the "SOC - Plateforme ELK" global dashboard in Zabbix 8.0.

The dashboard only needs ONE host group containing every host of the platform
(Elasticsearch cluster host, Elasticsearch nodes, Logstash, Kibana, Kafka).
Widgets select their data by item name inside that group, so nothing else has to be configured.
Run the script again after adding or removing hosts in the group: graphs list the hosts explicitly.

Usage:
    ZABBIX_API_TOKEN=<token> python3 create_elk_dashboard.py \
        --url https://zabbix.example.org --group "Plateforme ELK"

Templates expected on the hosts of the group:
    Elasticsearch Cluster by HTTP (official), SOC Elasticsearch health report by HTTP,
    SOC Elasticsearch node by HTTP, SOC Elasticsearch data freshness by HTTP,
    SOC Logstash by Zabbix agent 2, SOC Kibana by HTTP,
    Apache Kafka by JMX (official), SOC Kafka consumer lag by Zabbix agent 2,
    Linux by Zabbix agent (official) for the "Système" page (CPU, memory, disks, bandwidth).
A missing template only leaves the matching widgets empty.
"""
import argparse
import json
import os
import ssl
import sys
import urllib.request

GREEN, YELLOW, ORANGE, RED, GREY = "0EC9AC", "FFD54F", "FF9800", "FF465C", "9E9E9E"
# Label macros: keep only the part of the item name that identifies the object.
IN_BRACKETS = '{{ITEM.NAME}.regsub("\\[(.*)\\]", "\\1")}'
ES_NODE = '{{ITEM.NAME}.regsub("^ES (.*): ", "\\1")}'
INT, STR, GROUP = 0, 1, 2
HOSTS = []  # names of the hosts of the platform group, used to scope the graphs


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
            sys.exit("Zabbix API error on %s: %s" % (method, json.dumps(data["error"])))
        return data["result"]


def field(ftype, name, value):
    return {"type": ftype, "name": name, "value": value}


def widget(wtype, name, x, y, w, h, fields):
    return {"type": wtype, "name": name, "x": x, "y": y, "width": w, "height": h,
            "view_mode": 0, "fields": fields}


def honeycomb(name, x, y, w, h, gid, item, thresholds, primary="{HOST.NAME}", decimals=0):
    """One cell per matching item of the group, coloured by thresholds [(value, colour), ...]."""
    fields = [field(GROUP, "groupids.0", gid), field(STR, "items.0", item),
              field(STR, "primary_label", primary), field(INT, "rf_rate", 60),
              field(INT, "primary_label_size_type", 1), field(INT, "primary_label_size", 16),
              field(INT, "secondary_label_size_type", 1), field(INT, "secondary_label_size", 14),
              field(INT, "secondary_label_decimal_places", decimals)]
    for i, (value, color) in enumerate(thresholds):
        fields += [field(STR, "thresholds.%d.color" % i, color),
                   field(STR, "thresholds.%d.threshold" % i, str(value))]
    return widget("honeycomb", name, x, y, w, h, fields)


def graph(name, x, y, w, h, datasets):
    """Line graph. datasets = [(item pattern, colour), ...], one line per matching host/item.

    Only the hosts of the platform group are drawn (HOSTS is filled in main)."""
    fields = [field(INT, "rf_rate", 60), field(INT, "legend_lines", 4)]
    for i, (item, color) in enumerate(datasets):
        for j, host in enumerate(HOSTS):
            fields.append(field(STR, "ds.%d.hosts.%d" % (i, j), host))
        fields += [field(STR, "ds.%d.items.0" % i, item),
                   field(STR, "ds.%d.color" % i, color), field(INT, "ds.%d.fill" % i, 1),
                   field(INT, "ds.%d.transparency" % i, 5)]
    return widget("svggraph", name, x, y, w, h, fields)


def problems(name, x, y, w, h, gid):
    return widget("problems", name, x, y, w, h,
                  [field(GROUP, "groupids.0", gid), field(INT, "show_lines", 25), field(INT, "rf_rate", 60)])


def build_pages(gid):
    es_status = [(0, RED), (1, ORANGE), (2, GREEN)]            # ES: Node status
    svc_state = [(0, RED), (1, RED), (2, YELLOW), (3, GREEN)]  # Logstash / Kibana: Service state
    health = [(0, GREEN), (1, YELLOW), (2, RED), (3, GREY)]    # ES health [*]: Status
    zero_ok = [(0, GREEN), (1, RED)]                           # counters that must stay at 0
    heap = [(0, GREEN), (85, YELLOW), (95, RED)]
    queue = [(0, GREEN), (60, YELLOW), (80, RED)]

    load = [(0, GREEN), (80, YELLOW), (90, RED)]
    lag = [(0, GREEN), (10000, YELLOW), (100000, RED)]
    age = [(0, GREEN), (300, YELLOW), (900, RED)]
    # Global view: one screen, top to bottom = alerts, state of each component, log chain, system.
    overview = [
        widget("problemsbysv", "Problèmes par sévérité", 0, 0, 18, 3,
               [field(GROUP, "groupids.0", gid), field(INT, "show_type", 1), field(INT, "rf_rate", 60)]),
        honeycomb("Elasticsearch - santé du cluster par indicateur", 18, 0, 30, 3, gid,
                  "ES health [*]: Status", health, primary=IN_BRACKETS),
        honeycomb("Kafka - partitions sous-répliquées", 48, 0, 12, 3, gid, "Under replicated partitions", zero_ok),
        honeycomb("Kafka - partitions hors ligne", 60, 0, 12, 3, gid, "Offline partitions count", zero_ok),
        honeycomb("Elasticsearch - statut de chaque serveur", 0, 3, 24, 4, gid, "ES: Node status", es_status),
        honeycomb("Logstash - état de chaque serveur", 24, 3, 18, 4, gid, "Logstash: Service state", svc_state),
        honeycomb("Kibana - état de chaque serveur", 42, 3, 12, 4, gid, "Kibana: Service state", svc_state),
        honeycomb("Kafka - consommateurs actifs par groupe", 54, 3, 18, 4, gid,
                  "Kafka lag [*]: Active consumers", [(0, RED), (1, GREEN)], primary=IN_BRACKETS),
        honeycomb("Chaîne de logs - messages en attente dans Kafka", 0, 7, 18, 4, gid,
                  "Kafka lag [*]: Messages waiting", lag, primary=IN_BRACKETS),
        honeycomb("Chaîne de logs - âge du dernier document dans Elasticsearch", 18, 7, 18, 4, gid,
                  "Log freshness [*]: Age of the latest document", age, primary=IN_BRACKETS),
        graph("Chaîne de logs - documents livrés à Elasticsearch par seconde", 36, 7, 36, 4,
              [("Logstash output [*]: Documents delivered per second", GREEN)]),
        honeycomb("CPU par serveur (%)", 0, 11, 18, 4, gid, "CPU utilization", load),
        honeycomb("Mémoire par serveur (%)", 18, 11, 18, 4, gid, "Memory utilization", load),
        graph("Bande passante par serveur (entrante / sortante)", 36, 11, 36, 4,
              [("Interface *: Bits received", "2196F3"), ("Interface *: Bits sent", GREEN)]),
        problems("Problèmes en cours sur la plateforme", 0, 15, 72, 5, gid),
    ]
    chain = [
        honeycomb("1. Kafka - messages en attente par groupe / topic", 0, 0, 24, 5, gid,
                  "Kafka lag [*]: Messages waiting", lag, primary=IN_BRACKETS),
        honeycomb("1. Kafka - consommateurs actifs par groupe / topic", 24, 0, 24, 5, gid,
                  "Kafka lag [*]: Active consumers", [(0, RED), (1, GREEN)], primary=IN_BRACKETS),
        honeycomb("3. Elasticsearch - âge du dernier document par motif d'index", 48, 0, 24, 5, gid,
                  "Log freshness [*]: Age of the latest document", [(0, GREEN), (300, YELLOW), (900, RED)],
                  primary=IN_BRACKETS),
        graph("1. Kafka - messages en attente", 0, 5, 36, 5, [("Kafka lag [*]: Messages waiting", ORANGE)]),
        graph("2. Logstash - événements émis par seconde, par pipeline", 36, 5, 36, 5,
              [("Logstash pipeline [*]: Events sent per second", "9C27B0")]),
        graph("2. Logstash vers Elasticsearch - documents livrés par seconde", 0, 10, 36, 5,
              [("Logstash output [*]: Documents delivered per second", GREEN)]),
        graph("2. Logstash vers Elasticsearch - envois en échec et documents rejetés", 36, 10, 36, 5,
              [("Logstash: Failed bulk requests to Elasticsearch", RED),
               ("Logstash output [*]: Documents rejected", ORANGE)]),
        graph("3. Elasticsearch - documents reçus sur 5 minutes, par motif d'index", 0, 15, 72, 5,
              [("Log freshness [*]: Documents in the last 5 minutes", "2196F3")]),
    ]
    elasticsearch = [
        honeycomb("Statut de chaque serveur", 0, 0, 36, 5, gid, "ES: Node status", es_status),
        honeycomb("Heap JVM par nœud (%)", 36, 0, 36, 5, gid,
                  "ES *: Percent of JVM heap currently in use", heap, primary=ES_NODE),
        honeycomb("Santé du cluster par indicateur", 0, 5, 72, 4, gid,
                  "ES health [*]: Status", health, primary=IN_BRACKETS),
        graph("Shards non assignés / en initialisation / en relocalisation", 0, 9, 36, 5,
              [("Number of unassigned shards", RED), ("Number of initializing shards", YELLOW),
               ("Number of relocating shards", "2196F3")]),
        graph("Latence d'indexation par nœud (ms)", 36, 9, 36, 5, [("ES *: Indexing latency", "2196F3")]),
        graph("Latence de recherche par nœud (ms)", 0, 14, 36, 5, [("ES *: Query latency", "9C27B0")]),
        graph("Tâches rejetées par nœud (write / search)", 36, 14, 36, 5,
              [("ES *: Write thread pool executor tasks rejected", RED),
               ("ES *: Search thread pool executor tasks rejected", ORANGE)]),
    ]
    logstash = [
        honeycomb("État de chaque serveur", 0, 0, 24, 5, gid, "Logstash: Service state", svc_state),
        honeycomb("Heap JVM (%)", 24, 0, 24, 5, gid, "Logstash: Percent of JVM heap currently in use", heap),
        honeycomb("Remplissage des files persistantes (%)", 48, 0, 24, 5, gid,
                  "Logstash pipeline [*]: Persistent queue usage", queue,
                  primary="{HOST.NAME} " + IN_BRACKETS, decimals=1),
        graph("Événements reçus par seconde, par serveur", 0, 5, 36, 5,
              [("Logstash: Events received per second", "2196F3")]),
        graph("Événements émis par seconde, par serveur", 36, 5, 36, 5,
              [("Logstash: Events sent per second", GREEN)]),
        graph("Événements émis par seconde, par pipeline", 0, 10, 36, 5,
              [("Logstash pipeline [*]: Events sent per second", "9C27B0")]),
        graph("Événements en attente dans les files", 36, 10, 36, 5,
              [("Logstash: Events waiting in queues", ORANGE)]),
    ]
    kafka = [
        honeycomb("Partitions sous-répliquées par broker", 0, 0, 24, 5, gid, "Under replicated partitions", zero_ok),
        honeycomb("Partitions hors ligne par broker", 24, 0, 24, 5, gid, "Offline partitions count", zero_ok),
        honeycomb("Partitions sous le minimum d'ISR par broker", 48, 0, 24, 5, gid,
                  "Under minimum ISR partition count", zero_ok),
        graph("Messages entrants par seconde, par broker", 0, 5, 36, 5, [("Messages in per second", "2196F3")]),
        graph("Octets entrants / sortants par seconde", 36, 5, 36, 5,
              [("Bytes in per second", GREEN), ("Bytes out per second", "9C27B0")]),
        graph("Disponibilité des threads de traitement (%)", 0, 10, 36, 5,
              [("Request handler average idle percent", GREEN)]),
        graph("Retard maximal de réplication", 36, 10, 36, 5, [("Replication maximum lag", ORANGE)]),
    ]
    kibana = [
        honeycomb("État de chaque serveur", 0, 0, 24, 5, gid, "Kibana: Service state", svc_state),
        graph("Temps de réponse moyen (ms)", 24, 0, 48, 5, [("Kibana: Average response time", "2196F3")]),
        graph("Connexions simultanées", 0, 5, 36, 5, [("Kibana: Concurrent connections", GREEN)]),
        graph("Mémoire heap utilisée", 36, 5, 36, 5, [("Kibana: Heap memory used", "9C27B0")]),
    ]
    system = [
        honeycomb("CPU par serveur (%)", 0, 0, 24, 5, gid, "CPU utilization", load),
        honeycomb("Mémoire par serveur (%)", 24, 0, 24, 5, gid, "Memory utilization", load),
        honeycomb("Systèmes de fichiers (% utilisé)", 48, 0, 24, 5, gid, "FS [*]: Space: Used, in %", load,
                  primary="{HOST.NAME} " + IN_BRACKETS),
        graph("Bande passante entrante par serveur et interface", 0, 5, 36, 6,
              [("Interface *: Bits received", "2196F3")]),
        graph("Bande passante sortante par serveur et interface", 36, 5, 36, 6,
              [("Interface *: Bits sent", GREEN)]),
        graph("CPU par serveur (%)", 0, 11, 36, 5, [("CPU utilization", ORANGE)]),
        graph("Mémoire par serveur (%)", 36, 11, 36, 5, [("Memory utilization", "9C27B0")]),
        graph("Charge système (1 min)", 0, 16, 36, 5, [("Load average (1m avg)", RED)]),
        graph("Erreurs et paquets rejetés sur les interfaces", 36, 16, 36, 5,
              [("Interface *: Inbound packets with errors", RED), ("Interface *: Outbound packets with errors", ORANGE),
               ("Interface *: Inbound packets discarded", YELLOW), ("Interface *: Outbound packets discarded", "9E9E9E")]),
    ]
    return [
        {"name": "Vue globale", "widgets": overview},
        {"name": "Chaîne de logs", "widgets": chain},
        {"name": "Elasticsearch", "widgets": elasticsearch},
        {"name": "Logstash", "widgets": logstash},
        {"name": "Kafka", "widgets": kafka},
        {"name": "Kibana", "widgets": kibana},
        {"name": "Système", "widgets": system},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="Zabbix frontend URL, e.g. https://zabbix.example.org")
    parser.add_argument("--group", required=True, help="Host group containing all hosts of the ELK platform")
    parser.add_argument("--name", default="SOC - Plateforme ELK", help="Dashboard name")
    parser.add_argument("--insecure", action="store_true", help="Do not verify the frontend TLS certificate")
    args = parser.parse_args()

    token = os.environ.get("ZABBIX_API_TOKEN")
    if not token:
        sys.exit("Set the ZABBIX_API_TOKEN environment variable to a Zabbix API token.")
    api = Api(args.url, token, verify=not args.insecure)

    groups = api.call("hostgroup.get", {"filter": {"name": args.group}, "output": ["groupid"]})
    if not groups:
        sys.exit("Host group not found: %s" % args.group)
    hosts = api.call("host.get", {"groupids": groups[0]["groupid"], "output": ["host"]})
    if not hosts:
        sys.exit("No host in group: %s" % args.group)
    HOSTS.extend(sorted(h["host"] for h in hosts))
    pages = build_pages(groups[0]["groupid"])

    existing = api.call("dashboard.get", {"filter": {"name": args.name}, "output": ["dashboardid"]})
    if existing:
        dashboardid = existing[0]["dashboardid"]
        api.call("dashboard.update", {"dashboardid": dashboardid, "pages": pages})
        action = "updated"
    else:
        dashboardid = api.call("dashboard.create", {
            "name": args.name, "display_period": 60, "auto_start": 0, "pages": pages})["dashboardids"][0]
        action = "created"
    print("Dashboard %s: %s/zabbix.php?action=dashboard.view&dashboardid=%s"
          % (action, args.url.rstrip("/"), dashboardid))


if __name__ == "__main__":
    main()
