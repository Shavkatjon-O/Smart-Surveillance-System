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

MAIN = {"group": "main"}

with Diagram(direction="LR", show=False):
    with Cluster("Site LAN"):
        cameras = IotCamera("Cameras")
    with Cluster("Compose stack"):
        bridges = IotDeviceGateway("V380 bridges\nv380-ptz")
        media = Server("Media server\ngo2rtc")
        analytics = IotGeneric("Analytics\nanalytics")
        backend = Fastapi("API\nbackend", **MAIN)
        bus = Nats("Event bus\nnats")
        database = Postgresql("Database\npostgres")
        web = Nginx("Web\nweb", **MAIN)
        volume = Storage("Media files\nevents volume")
    with Cluster("Phones and browsers") as clients:
        console = Client("Web console", **MAIN)
        phone = Flutter("Mobile app")

    web << Edge(label="HTTPS", main="true", lhead=clients.name) << console
    backend << Edge(label="API", main="true") << web
    backend >> Edge(label="Events") >> bus
    backend >> Edge(label="SQL", main="true") >> database
    backend >> Edge(label="Reads", style="dashed") >> volume
    cameras << Edge(label="ONVIF") << bridges
    bridges << Edge(label="PTZ, siren") << backend
    media << Edge(label="Streams") << backend
    analytics >> Edge(label="Config poll") >> backend
    analytics >> Edge(label="Clips", minlen="2") >> volume
    web >> Edge(style="invis") >> phone
