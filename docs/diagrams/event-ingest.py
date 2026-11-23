"""Event ingest: how producers put event changes on the bus and the event-store worker stores them."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.iot import IotGeneric
from diagrams.onprem.database import Postgresql
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi

MAIN = {"group": "main"}

with Diagram(direction="LR", show=False):
    with Cluster("Producers"):
        analytics = IotGeneric("Analytics\nDeepStream", **MAIN)
        api = Fastapi("API\nONVIF, intake")
    with Cluster("Event bus", graph_attr={"icon": Nats}):
        events = Node("Events\nSSS_EVENTS", **MAIN)
        deadletter = Node("Dead letters\nSSS_DEADLETTER")
    with Cluster("Workers"):
        store = Node("Event store", **MAIN)
    database = Postgresql("Database\nPostgreSQL", **MAIN)

    analytics >> Edge(label="*.analytics.*.event", mono="true", main="true") >> events
    api >> Edge(label="*.camera.*.event", mono="true") >> events
    events >> Edge(main="true") >> store
    store >> Edge(label="Upsert", main="true") >> database
    deadletter << Edge(label="Exhausted", style="dashed") << store
