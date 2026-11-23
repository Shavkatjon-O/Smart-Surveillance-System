"""Platform overview: how the products and services fit together."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotGeneric
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter

MAIN = {"group": "main"}

with Diagram(direction="LR", show=False, graph_attr={"ranksep": "0.3"}):
    with Cluster("Site LAN"):
        cameras = IotCamera("Cameras", **MAIN)
        tv = Android("TV app")
    with Cluster("Server"):
        media = Server("Media server\ngo2rtc", **MAIN)
        analytics = IotGeneric("Analytics\nDeepStream", **MAIN)
        bus = Nats("Event bus\nNATS JetStream", **MAIN)
        database = Postgresql("Database\nPostgreSQL")
        api = Fastapi("API\nFastAPI", **MAIN)
    with Cluster("Phones and browsers") as clients:
        console = Client("Console", **MAIN)
        phone = Flutter("Mobile app")

    cameras >> Edge(main="true") >> media
    cameras >> Edge(style="dashed", constraint="false") >> tv
    media >> Edge(main="true") >> analytics
    analytics >> Edge(label="Events", main="true") >> bus
    bus >> Edge(main="true") >> api
    database << Edge(label="SQL") << api
    api >> Edge(label="HTTPS", main="true", lhead=clients.name) >> console
    api >> Edge(style="invis") >> phone
