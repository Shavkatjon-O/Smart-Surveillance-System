"""Architecture, requests and control: who calls the API and what the API drives."""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.custom import Custom
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.network import Nginx
from diagrams.programming.framework import Fastapi, Flutter

MARK = str(Path(__file__).resolve().parents[2] / "packages" / "brand" / "mark-dark.svg")
MAIN = {"group": "main"}

with Diagram(direction="LR", show=False):
    with Cluster("Site LAN"):
        cameras = IotCamera("Cameras\nONVIF, EZVIZ")
        v380 = IotCamera("V380 cameras")
    with Cluster("Remote site"):
        connector = Custom("Connector", MARK)
    with Cluster("Server"):
        bridges = IotDeviceGateway("V380 bridges")
        media = Server("Media server\ngo2rtc")
        analytics = IotGeneric("Analytics\nDeepStream")
        api = Fastapi("API\nFastAPI", **MAIN)
        database = Postgresql("Database\nPostgreSQL")
        web = Nginx("Web\nnginx", **MAIN)
    with Cluster("Phones and browsers") as clients:
        console = Client("Web console", **MAIN)
        phone = Flutter("Mobile app")

    web << Edge(label="HTTPS", main="true", lhead=clients.name) << console
    api << Edge(label="API", main="true") << web
    api >> Edge(label="SQL", main="true") >> database
    cameras << Edge(label="ONVIF") << api
    bridges << Edge(label="PTZ, siren") << api
    v380 << Edge(label="V380") << bridges
    media << Edge(label="Streams") << api
    analytics >> Edge(label="Config poll") >> api
    connector >> Edge(label="Heartbeats") >> api
    web >> Edge(style="invis") >> phone
