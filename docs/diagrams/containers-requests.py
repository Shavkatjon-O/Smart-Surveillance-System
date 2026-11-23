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
        analytics = IotGeneric("Analytics\nanalytics", grid="2,0")
        media = Server("Media server\ngo2rtc", grid="1,1")
        bridges = IotDeviceGateway("V380 bridges\nv380-ptz", grid="1,3.4")
        volume = Storage("Media files\nevents volume", grid="1,0")
        backend = Fastapi("API\nbackend", grid="2,2")
        database = Postgresql("Database\npostgres", grid="2,3.4")
        web = Nginx("Web\nweb", grid="3,2")
        bus = Nats("Event bus\nnats", grid="3,3.4")
    with Cluster("Phones and browsers"):
        console = Client("Web console", grid="4,2")
        phone = Flutter("Mobile app", grid="4,3")

    console >> Edge(step="Console and app over HTTPS", main="true") >> web
    web >> Edge(step="nginx proxies /api to backend", main="true") >> backend
    backend >> Edge(step="SQL to postgres", main="true") >> database
    backend >> Edge(step="Camera events out, live events in") >> bus
    analytics >> Edge(step="Snapshots and clips") >> volume
    analytics >> Edge(step="Configuration poll, faces") >> backend
    backend >> Edge(step="Registers camera streams") >> media
    backend >> Edge(step="ONVIF events, settings, PTZ") >> cameras
    backend >> Edge(step="PTZ, light and siren") >> bridges
