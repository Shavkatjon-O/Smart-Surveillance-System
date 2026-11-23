"""Containers, video: the video between the containers, the cameras and the players."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.programming.framework import Flutter

MAIN = {"group": "main"}

with Diagram(direction="LR", show=False):
    with Cluster("Site LAN"):
        v380 = IotCamera("V380 cameras")
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", **MAIN)
    with Cluster("Compose stack"):
        bridges = IotDeviceGateway("V380 bridges\nor relays")
        analytics = IotGeneric("Analytics\nDeepStream")
        through = Node(waypoint="true", **MAIN)
        media = Server("Media server\ngo2rtc", **MAIN)
    with Cluster("Players"):
        console = Client("Web console\nvia nginx", **MAIN)
        phone = Flutter("Mobile app")
        tv = Android("TV app\nAndroid TV")

    v380 >> Edge(label="V380") >> bridges
    cameras >> Edge(label="RTSP", main="true", arrowhead="none") >> through
    through >> Edge(main="true") >> media
    bridges >> Edge() >> media
    analytics << Edge(label="H.265, NVDEC") << media
    media >> Edge(label="H.264, NVENC", main="true") >> console
    media >> Edge(label="WebRTC") >> phone
    media >> Edge(label="Composed", style="dashed") >> tv
