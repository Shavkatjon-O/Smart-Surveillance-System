"""Platform overview: how the products and services fit together."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.generic.os import Android
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter

BOTTOM = {"labelloc": "b"}

with Diagram(direction="TB", show=False):
    with Cluster("Site LAN"):
        cameras = IotCamera("Cameras: ONVIF,\nEZVIZ, RTSP, V380")
        tv = Android("TV app")
    with Cluster("Phones and browsers"):
        console = Client("Console\nin the browser")
        mobile = Flutter("Mobile app")
    with Cluster("Server", graph_attr=BOTTOM):
        bridge = IotDeviceGateway("V380 bridge")
        go2rtc = Server("go2rtc")
        analytics = IotGeneric("Analytics engine\nDeepStream")
        api = Fastapi("Platform API\nFastAPI")
        nats = Nats("Event bus\nNATS JetStream")
        postgres = Postgresql("Postgres")

    cameras >> Edge(label="RTSP, V380 on\nthe local Wi-Fi", style="dashed", constraint="false") >> tv
    cameras >> Edge(label="V380\nprotocol") >> bridge
    cameras >> Edge(label="RTSP", minlen="2") >> go2rtc
    bridge >> Edge(label="RTSP") >> go2rtc
    go2rtc >> Edge(label="RTSP: original\nstreams") >> analytics
    console >> Edge(label="HTTPS: API,\nvideo, SSE") >> api
    mobile >> Edge(label="HTTPS: API,\nWebRTC") >> api
    api << Edge(label="NATS:\nevents") << nats
    nats << Edge(label="NATS:\nevents") << analytics
    api >> Edge(label="SQL") >> postgres
    api << Edge(label="HTTP: config\nevery 5 s") << analytics
