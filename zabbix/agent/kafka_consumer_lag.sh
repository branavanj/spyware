#!/bin/sh
# Consumer lag of every Kafka consumer group, as JSON, for Zabbix agent 2.
# One entry per (group, topic): total lag, number of partitions, partitions without
# an active consumer, number of active consumers.
#
# Usage: kafka_consumer_lag.sh <bootstrap-server> [command-config-file]
#   <bootstrap-server>     e.g. localhost:9092
#   [command-config-file]  optional client properties file (SASL / TLS settings)
#
# KAFKA_BIN can be set in the environment if Kafka is not installed in /opt/kafka/bin.

BOOTSTRAP="${1:-localhost:9092}"
CONFIG="$2"
KAFKA_BIN="${KAFKA_BIN:-/opt/kafka/bin}"

# The broker's own JMX settings must not leak into this short-lived JVM (port clash).
unset JMX_PORT KAFKA_JMX_OPTS KAFKA_OPTS
export KAFKA_HEAP_OPTS="-Xmx128m"

if [ -n "$CONFIG" ]; then
    OUT=$("$KAFKA_BIN/kafka-consumer-groups.sh" --bootstrap-server "$BOOTSTRAP" --command-config "$CONFIG" --describe --all-groups 2>/dev/null)
else
    OUT=$("$KAFKA_BIN/kafka-consumer-groups.sh" --bootstrap-server "$BOOTSTRAP" --describe --all-groups 2>/dev/null)
fi
RC=$?
if [ $RC -ne 0 ] || printf '%s\n' "$OUT" | grep -q '^Error:'; then
    echo "kafka-consumer-groups.sh failed (exit code $RC)" >&2
    exit 1
fi

# Columns: GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG CONSUMER-ID HOST CLIENT-ID
printf '%s\n' "$OUT" | awk '
$1 == "GROUP" || NF < 7 || $3 !~ /^[0-9]+$/ { next }
{
    key = $1 "\t" $2
    if (!(key in parts)) { order[++n] = key }
    parts[key]++
    if ($6 ~ /^[0-9]+$/) { lag[key] += $6 }
    if ($7 == "-") { orphan[key]++ } else if (!((key "\t" $7) in seen)) { seen[key "\t" $7] = 1; members[key]++ }
}
END {
    printf "["
    for (i = 1; i <= n; i++) {
        key = order[i]
        split(key, f, "\t")
        printf "%s{\"group\":\"%s\",\"topic\":\"%s\",\"lag\":%d,\"partitions\":%d,\"unassigned\":%d,\"members\":%d}", (i > 1 ? "," : ""), f[1], f[2], lag[key], parts[key], orphan[key], members[key]
    }
    printf "]\n"
}'
