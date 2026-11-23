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

with Diagram(show=False, graph_attr={"column": "172", "row": "110"}):
    with Cluster("Site LAN"):
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", grid="-0.1,2")
        v380 = IotCamera("V380 cameras", grid="-0.1,3")
    with Cluster("Remote site"):
        connector = Custom("Connector", MARK, grid="-0.1,4.4")
    with Cluster("Server"):
        analytics = IotGeneric("Analytics\nDeepStream", grid="2,0.75")
        media = Server("Media server\ngo2rtc", grid="1,0.75")
        bridges = IotDeviceGateway("V380 bridges", grid="1,3")
        api = Fastapi("API\nFastAPI", grid="2,2")
        database = Postgresql("Database\nPostgreSQL", grid="2,3.35")
        web = Nginx("Web\nnginx", grid="2.88,2")
    with Cluster("Phones and browsers"):
        console = Client("Web console", grid="3.9,2")
        phone = Flutter("Mobile app", grid="3.9,3")

    console >> Edge(step="Web console and mobile app call the API over HTTPS", main="true") >> web
    web >> Edge(step="Web passes /api requests to the API", main="true") >> api
    api >> Edge(step="API reads and writes PostgreSQL", main="true") >> database
    analytics >> Edge(step="Analytics polls its configuration every 5 s") >> api
    api >> Edge(step="API registers camera streams in the media server") >> media
    api >> Edge(step="ONVIF events, settings and PTZ") >> cameras
    api >> Edge(step="PTZ, light and siren of V380 cameras") >> bridges
    bridges >> Edge(step="V380 protocol") >> v380
    connector >> Edge(step="Connector heartbeats and discovery over HTTPS") >> web
