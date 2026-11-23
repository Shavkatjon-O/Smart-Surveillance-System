"""Architecture, video: where each video stream comes from and who plays it."""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.custom import Custom
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.network import Nginx
from diagrams.programming.framework import Flutter

MARK = str(Path(__file__).resolve().parents[2] / "packages" / "brand" / "mark-dark.svg")
BOTTOM = {"labelloc": "b"}

with Diagram(direction="TB", show=False):
    with Cluster("Site LAN"):
        cameras = IotCamera("EZVIZ and\nONVIF cameras")
        v380 = IotCamera("V380 cameras")
        tv = Android("Android TV app")
    with Cluster("Remote site"):
        remote = IotCamera("Site cameras")
        connector = Custom("connector\nand its go2rtc", MARK)
    with Cluster("Server", graph_attr=BOTTOM):
        bridges = IotDeviceGateway("v380 bridges")
        go2rtc = Server("go2rtc")
        analytics = IotGeneric("analytics\nDeepStream")
        web = Nginx("web\nnginx")
    with Cluster("Phones and browsers", graph_attr=BOTTOM):
        browser = Client("Browser")
        phone = Flutter("Mobile app")

    cameras >> Edge(label="RTSP") >> tv
    v380 >> Edge(label="V380 protocol") >> tv
    v380 >> Edge(label="V380 protocol") >> bridges
    cameras >> Edge(label="RTSP: H.265", minlen="2") >> go2rtc
    bridges >> Edge(label="RTSP") >> go2rtc
    remote >> Edge(label="RTSP") >> connector
    connector >> Edge(label="RTSP restream") >> go2rtc
    go2rtc >> Edge(label="RTSP: original H.265") >> analytics
    go2rtc >> Edge(label="websocket: H.264,\nNVENC transcode") >> web
    web >> Edge(label="HTTPS websocket:\n/go2rtc") >> browser
    go2rtc >> Edge(label="WebRTC: H.264,\nUDP 8555", minlen="2") >> phone
