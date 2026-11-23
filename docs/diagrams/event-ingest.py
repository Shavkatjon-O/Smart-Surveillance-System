"""Event ingest: how producers put event changes on the bus and the event-store worker stores them."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.iot import IotGeneric
from diagrams.onprem.database import Postgresql
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi

with Diagram(show=False, graph_attr={"row": "110"}):
    with Cluster("Producers"):
        analytics = IotGeneric("Analytics\nDeepStream", grid="0,0")
        api = Fastapi("API\nONVIF, intake", grid="0,1")
    with Cluster("Event bus", graph_attr={"icon": Nats}):
        events = Node("Events\nSSS_EVENTS", grid="1,0")
        deadletter = Node("Dead letters\nSSS_DEADLETTER", grid="1,1")
    with Cluster("Workers"):
        store = Node("Event store", grid="2,0")
    database = Postgresql("Database\nPostgreSQL", grid="3,0")

    (
        analytics
        >> Edge(step="Analytics publishes event changes on sss.<org>.<site>.analytics.<view>.event", main="true")
        >> events
    )
    (
        api
        >> Edge(step="API publishes camera events on sss.<org>.<site>.camera.<camera>.event and edge engine events")
        >> events
    )
    events >> Edge(step="Event store reads SSS_EVENTS with a durable consumer", main="true") >> store
    store >> Edge(step="Event store upserts each event by its id, so a repeat changes nothing", main="true") >> database
    store >> Edge(step="Exhausted messages go to sss.deadletter.<stream>.<consumer>", style="dashed") >> deadletter
