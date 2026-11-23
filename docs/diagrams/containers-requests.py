"""Containers, requests: the calls between the containers of the compose stack."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.network import Nginx
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter

BOTTOM = {"labelloc": "b"}

with Diagram(direction="TB", show=False):
    with Cluster("Phones and browsers"):
        browser = Client("Browser")
        phone = Flutter("Mobile app")
    with Cluster("Compose stack", graph_attr=BOTTOM):
        analytics = IotGeneric("analytics\nDeepStream")
        web = Nginx("web\nnginx, console")
        backend = Fastapi("backend\nFastAPI")
        nats = Nats("nats\nJetStream")
        events = Storage("events volume\nsnapshots, clips,\nread by backend")
        postgres = Postgresql("postgres")
        go2rtc = Server("go2rtc")
        bridges = IotDeviceGateway("v380 bridges")
    with Cluster("Site LAN", graph_attr=BOTTOM):
        cameras = IotCamera("Cameras")

    browser >> Edge(label="HTTPS") >> web
    phone >> Edge(label="HTTPS:\nAPI, SSE") >> web
    web >> Edge(label="HTTP: /api") >> backend
    analytics >> Edge(label="HTTP: /internal\nconfig and faces,\nstatus") << backend
    analytics >> Edge(label="NATS:\nevents") >> nats
    analytics >> Edge(label="files:\nJPEG, MP4") >> events
    backend >> Edge(label="NATS: publish,\nconsume", constraint="false") << nats
    backend >> Edge(label="SQL") >> postgres
    backend >> Edge(label="HTTP: streams,\n/internal/tv") << go2rtc
    backend >> Edge(label="ONVIF,\nsiren API") >> bridges
    backend >> Edge(label="ONVIF, health\nprobes") >> cameras
