"""Event delivery: how stored events become alerts, usage and live updates."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.iot import IotGeneric
from diagrams.firebase.grow import Messaging
from diagrams.onprem.client import Client
from diagrams.onprem.database import Postgresql
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter
from diagrams.saas.chat import Telegram

ALERTS = {"group": "alerts"}
LIVE = {"group": "live"}

with Diagram(direction="LR", show=False):
    with Cluster("Producers"):
        store = Node("Event store", **ALERTS)
        analytics = IotGeneric("Analytics\nDeepStream")
    with Cluster("Event bus", graph_attr={"icon": Nats}):
        alerts = Node("Alerts\nSSS_ALERTS", **ALERTS)
        usage = Node("Usage\nSSS_USAGE")
        live = Node("Live\nno stream", dashed="true", **LIVE)
        observations = Node("Observations\nSSS_OBSERVATIONS")
    with Cluster("Workers"):
        notifier = Node("Notifier\nTelegram, push", **ALERTS)
        metering = Node("Metering")
    with Cluster("API replicas"):
        relay = Fastapi("Live fan-out", **LIVE)
    with Cluster("Alert services") as services:
        telegram = Telegram("Telegram", **ALERTS)
        push = Messaging("Push\nFCM, ntfy")
    database = Postgresql("Database\nPostgreSQL")
    with Cluster("Phones and browsers") as clients:
        phone = Flutter("Mobile app", **LIVE)
        browser = Client("Web console")

    store >> Edge(label="*.alert", mono="true", main="true") >> alerts
    store >> Edge(label="*.live", mono="true", main="true") >> live
    analytics >> Edge(label="*.observation", mono="true") >> observations
    alerts >> Edge(main="true") >> notifier
    notifier >> Edge(main="true", lhead=services.name) >> telegram
    usage << Edge(label="*.usage", mono="true") << notifier
    usage >> Edge() >> metering
    metering >> Edge(label="Rollups") >> database
    live >> Edge(main="true") >> relay
    observations >> Edge() >> relay
    relay >> Edge(label="SSE", main="true", lhead=clients.name) >> phone
    notifier >> Edge(style="invis") >> push
    relay >> Edge(style="invis") >> browser
