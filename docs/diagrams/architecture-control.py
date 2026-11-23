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
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", grid="0,2")
        v380 = IotCamera("V380 cameras", grid="0,3")
    with Cluster("Remote site"):
        connector = Custom("Connector", MARK, grid="0,4.4")
    with Cluster("Server"):
        analytics = IotGeneric("Analytics\nDeepStream", grid="2,0.85")
        media = Server("Media server\ngo2rtc", grid="1,0.85")
        bridges = IotDeviceGateway("V380 bridges", grid="1,3")
        api = Fastapi("API\nFastAPI", grid="2,2")
        database = Postgresql("Database\nPostgreSQL", grid="2,3.2")
        web = Nginx("Web\nnginx", grid="3,2")
    with Cluster("Phones and browsers"):
        console = Client("Web console", grid="4,2")
        phone = Flutter("Mobile app", grid="4,3")

    console >> Edge(step="Console and app call the API over HTTPS", main="true") >> web
    web >> Edge(step="nginx proxies /api to the API", main="true") >> api
    api >> Edge(step="Reads and writes PostgreSQL", main="true") >> database
    analytics >> Edge(step="Configuration poll every 5 s") >> api
    api >> Edge(step="Registers camera streams in go2rtc") >> media
    api >> Edge(step="ONVIF events, settings and PTZ") >> cameras
    api >> Edge(step="PTZ, light and siren of V380 cameras") >> bridges
    bridges >> Edge(step="V380 protocol") >> v380
    connector >> Edge(step="Heartbeats and discovery over HTTPS") >> web
