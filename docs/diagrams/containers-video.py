"""Containers, video: the video between the containers, the cameras and the players."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.network import Nginx
from diagrams.programming.framework import Flutter

BOTTOM = {"labelloc": "b"}

with Diagram(direction="TB", show=False):
    with Cluster("Site LAN"):
        cameras = IotCamera("EZVIZ and\nONVIF cameras")
        v380 = IotCamera("V380 cameras")
        tv = Android("Android TV app")
    with Cluster("Compose stack", graph_attr=BOTTOM):
        bridges = IotDeviceGateway("v380 bridges, or\nv380 relays with\nv380-native")
        go2rtc = Server("go2rtc\nRTSP 8554\nWebRTC 8555")
        analytics = IotGeneric("analytics\nDeepStream")
        web = Nginx("web\nnginx")
    with Cluster("Phones and browsers", graph_attr=BOTTOM):
        browser = Client("Browser")
        phone = Flutter("Mobile app")

    cameras >> Edge(label="RTSP") >> tv
    v380 >> Edge(label="V380") >> tv
    v380 >> Edge(label="V380 protocol") >> bridges
    cameras >> Edge(label="RTSP: H.265", minlen="2") >> go2rtc
    bridges >> Edge(label="RTSP") >> go2rtc
    go2rtc >> Edge(label="RTSP composed\nTV streams", style="dashed", constraint="false") >> tv
    go2rtc >> Edge(label="RTSP: original\nH.265, NVDEC") >> analytics
    go2rtc >> Edge(label="websocket: H.264\nNVENC transcode") >> web
    web >> Edge(label="HTTPS: /go2rtc\nwebsocket") >> browser
    go2rtc >> Edge(label="WebRTC:\nH.264", minlen="2") >> phone
