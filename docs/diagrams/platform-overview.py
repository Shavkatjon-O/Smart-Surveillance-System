"""Platform overview: how the products and services fit together."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotGeneric
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter

with Diagram(show=False, graph_attr={"column": "140", "row": "125"}):
    with Cluster("Site LAN"):
        cameras = IotCamera("Cameras", grid="0,0")
        tv = Android("TV app", grid="0,1")
    with Cluster("Server"):
        media = Server("Media server\ngo2rtc", grid="1,0")
        analytics = IotGeneric("Analytics\nDeepStream", grid="2,0")
        bus = Nats("Event bus\nNATS JetStream", grid="3,0")
        api = Fastapi("API\nFastAPI", grid="4,0")
        database = Postgresql("Database\nPostgreSQL", grid="4,1")
    with Cluster("Phones and browsers"):
        console = Client("Console", grid="5,0")
        phone = Flutter("Mobile app", grid="5,1")

    cameras >> Edge(step="go2rtc pulls each camera once", main="true") >> media
    media >> Edge(step="The original stream to the analytics", main="true") >> analytics
    analytics >> Edge(step="Events on the event bus", main="true") >> bus
    bus >> Edge(step="Stored by workers, relayed live by the API", main="true") >> api
    api >> Edge(step="Sites, cameras and events in PostgreSQL") >> database
    api >> Edge(step="Console and app call the API", main="true") >> console
    cameras >> Edge(step="The TV app plays the cameras on the Wi-Fi", style="dashed") >> tv
