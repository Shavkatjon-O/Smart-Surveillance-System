"""Architecture, video: where each video stream comes from and who plays it."""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.custom import Custom
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.programming.framework import Flutter

MARK = str(Path(__file__).resolve().parents[2] / "packages" / "brand" / "mark-dark.svg")
MAIN = {"group": "main"}

with Diagram(direction="LR", show=False):
    with Cluster("Site LAN"):
        v380 = IotCamera("V380 cameras")
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", **MAIN)
        tv = Android("TV app\nAndroid TV")
    with Cluster("Remote site"):
        remote = IotCamera("Cameras")
        connector = Custom("Connector\ngo2rtc", MARK)
    with Cluster("Server"):
        bridges = IotDeviceGateway("V380 bridges")
        analytics = IotGeneric("Analytics\nDeepStream")
        through = Node(waypoint="true", **MAIN)
        media = Server("Media server\ngo2rtc", **MAIN)
    with Cluster("Phones and browsers"):
        console = Client("Web console\nvia nginx", **MAIN)
        phone = Flutter("Mobile app")

    v380 >> Edge(label="V380") >> bridges
    cameras >> Edge(label="RTSP", main="true", arrowhead="none") >> through
    through >> Edge(main="true") >> media
    cameras >> Edge(style="dashed", constraint="false") >> tv
    bridges >> Edge() >> media
    remote >> Edge() >> connector
    connector >> Edge(label="Restream") >> media
    analytics << Edge(label="H.265") << media
    media >> Edge(label="Websocket", main="true") >> console
    media >> Edge(label="WebRTC", main="true") >> phone
