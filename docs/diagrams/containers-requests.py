"""Containers, requests: the calls between the containers of the compose stack."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.network import Nginx
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter

with Diagram(show=False, graph_attr={"column": "172", "row": "110"}):
    with Cluster("Site LAN"):
        cameras = IotCamera("Cameras", grid="0,2")
    with Cluster("Compose stack"):
        analytics = IotGeneric("Analytics\nDeepStream", grid="2,0")
        media = Server("Media server\ngo2rtc", grid="1,1")
        bridges = IotDeviceGateway("V380 bridges", grid="1,3.4")
        volume = Storage("Media files\nevents volume", grid="1,0")
        backend = Fastapi("API\nFastAPI", grid="2,2")
        database = Postgresql("Database\nPostgreSQL", grid="2,3.4")
        web = Nginx("Web\nnginx", grid="3,2")
        bus = Nats("Event bus\nNATS JetStream", grid="3,0.75")
    with Cluster("Phones and browsers"):
        console = Client("Web console", grid="4,2")
        phone = Flutter("Mobile app", grid="4,3")

    console >> Edge(step="Web console and mobile app call the API over HTTPS", main="true") >> web
    web >> Edge(step="Web passes /api requests to the API", main="true") >> backend
    backend >> Edge(step="API reads and writes PostgreSQL", main="true") >> database
    backend >> Edge(step="API publishes camera events and relays live events") >> bus
    analytics >> Edge(step="Analytics writes snapshots and clips") >> volume
    analytics >> Edge(step="Analytics polls its configuration and posts faces") >> backend
    backend >> Edge(step="API registers camera streams in the media server") >> media
    backend >> Edge(step="ONVIF events, settings and PTZ") >> cameras
    backend >> Edge(step="PTZ, light and siren of V380 cameras") >> bridges
