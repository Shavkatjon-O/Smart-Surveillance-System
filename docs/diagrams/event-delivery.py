"""Event delivery: how stored events become alerts, usage and live updates."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.iot import IotGeneric
from diagrams.firebase.grow import Messaging
from diagrams.onprem.client import Client
from diagrams.onprem.database import Postgresql
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter
from diagrams.saas.chat import Telegram

with Diagram(show=False, graph_attr={"column": "185", "row": "100"}):
    with Cluster("Producers"):
        analytics = IotGeneric("Analytics\nDeepStream", grid="0,0")
        store = Node("Event store", grid="0,1")
    with Cluster("Event bus", graph_attr={"icon": Nats}):
        observations = Node("Observations\nSSS_OBSERVATIONS", grid="1,0")
        live = Node("Live\nno stream", dashed="true", grid="1,1")
        alerts = Node("Alerts\nSSS_ALERTS", grid="1,2.45")
        usage = Node("Usage\nSSS_USAGE", grid="1,3.55")
    with Cluster("API replicas"):
        relay = Fastapi("Live fan-out", grid="2,0.5")
    with Cluster("Workers"):
        notifier = Node("Notifier\nTelegram, push", grid="2,2.45")
        metering = Node("Metering", grid="2,3.55")
    with Cluster("Phones and browsers"):
        console = Client("Web console", grid="3,0")
        phone = Flutter("Mobile app", grid="3,1")
    with Cluster("Alert services"):
        telegram = Telegram("Telegram", grid="3,2.45")
        push = Messaging("Push\nFCM, ntfy", grid="3.7,2.45")
    database = Postgresql("Database\nPostgreSQL", grid="3,3.6")

    store >> Edge(step="Stored events on sss.<org>.<site>.live", main="true") >> live
    live >> Edge(step="Every API replica listens to the live subject", main="true") >> relay
    relay >> Edge(step="Server-sent events to the console and app", main="true") >> console
    analytics >> Edge(step="View status every 5 s, last one kept") >> observations
    observations >> Edge(step="Replicas follow the last observation") >> relay
    store >> Edge(step="One alert per event on sss.<org>.alert", main="true") >> alerts
    alerts >> Edge(step="Telegram and push consumers", main="true") >> notifier
    notifier >> Edge(step="Sent with retries per recipient", main="true") >> telegram
    notifier >> Edge(step="Notifications sent on sss.<org>.usage") >> usage
    usage >> Edge(step="Metering reads SSS_USAGE") >> metering
    metering >> Edge(step="Hourly usage rollups") >> database
