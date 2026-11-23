"""Containers, video: the video between the containers, the cameras and the players."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.programming.framework import Flutter

with Diagram(show=False, graph_attr={"row": "110"}):
    with Cluster("Site LAN"):
        v380 = IotCamera("V380 cameras", grid="0,0")
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", grid="0,1")
    with Cluster("Compose stack"):
        bridges = IotDeviceGateway("V380 bridges\nor relays", grid="1,0")
        media = Server("Media server\ngo2rtc", grid="2,1")
    with Cluster("Consumers"):
        analytics = IotGeneric("Analytics\nDeepStream", grid="3,0")
        console = Client("Web console\nvia nginx", grid="3,1")
        phone = Flutter("Mobile app", grid="3,2")

    cameras >> Edge(step="RTSP from the cameras, each read once", main="true") >> media
    v380 >> Edge(step="V380 protocol, or the relays' own client") >> bridges
    bridges >> Edge(step="RTSP on 8554 inside the stack") >> media
    media >> Edge(step="Original H.265 over RTSP, decoded on NVDEC") >> analytics
    media >> Edge(step="H.264 from NVENC over a websocket", main="true") >> console
    media >> Edge(step="WebRTC on 8555 on the LAN address") >> phone
