"""Architecture, video: where each video stream comes from and who plays it."""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.custom import Custom
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.programming.framework import Flutter

MARK = str(Path(__file__).resolve().parents[2] / "packages" / "brand" / "mark-dark.svg")

with Diagram(show=False, graph_attr={"row": "110"}):
    with Cluster("Site LAN"):
        v380 = IotCamera("V380 cameras", grid="0,0")
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", grid="0,1")
        tv = Android("TV app\nAndroid TV", grid="0,2.2")
    with Cluster("Remote site"):
        remote = IotCamera("Cameras", grid="1,2.45")
        connector = Custom("Connector\ngo2rtc", MARK, grid="2,2.45")
    with Cluster("Server"):
        bridges = IotDeviceGateway("V380 bridges", grid="1,0")
        media = Server("Media server\ngo2rtc", grid="2,1")
    with Cluster("Consumers"):
        analytics = IotGeneric("Analytics\nDeepStream", grid="3,0")
        console = Client("Web console\nvia nginx", grid="3,1")
        phone = Flutter("Mobile app", grid="3,2")

    cameras >> Edge(step="RTSP, the original stream of each camera", main="true") >> media
    v380 >> Edge(step="V380 protocol to the bridges") >> bridges
    bridges >> Edge(step="RTSP from each V380 camera") >> media
    remote >> Edge(step="RTSP inside the remote site") >> connector
    connector >> Edge(step="Restream of the remote cameras") >> media
    media >> Edge(step="Original H.265 to the analytics") >> analytics
    media >> Edge(step="H.264 over a websocket to the console", main="true") >> console
    media >> Edge(step="WebRTC to the mobile app", main="true") >> phone
    cameras >> Edge(step="The TV app plays the cameras directly", style="dashed") >> tv
