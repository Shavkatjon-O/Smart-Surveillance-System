"""Architecture, requests and control: who calls the API and what the API drives."""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera, IotDeviceGateway, IotGeneric
from diagrams.custom import Custom
from diagrams.firebase.grow import Messaging
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.network import Nginx
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter
from diagrams.saas.chat import Telegram

MARK = str(Path(__file__).resolve().parents[2] / "packages" / "brand" / "mark-dark.svg")
BOTTOM = {"labelloc": "b"}

with Diagram(direction="TB", show=False):
    with Cluster("Phones and browsers"):
        browser = Client("Browser\nweb console")
        phone = Flutter("Mobile app")
    with Cluster("Remote site"):
        connector = Custom("connector", MARK)
    with Cluster("Server"):
        analytics = IotGeneric("analytics\nDeepStream")
        web = Nginx("web\nnginx")
        backend = Fastapi("backend\nFastAPI")
        nats = Nats("NATS\nJetStream")
        postgres = Postgresql("postgres")
        go2rtc = Server("go2rtc")
        bridges = IotDeviceGateway("v380 bridges")
    with Cluster("Alert services", graph_attr=BOTTOM):
        telegram = Telegram("Telegram")
        fcm = Messaging("FCM")
    with Cluster("Site LAN", graph_attr=BOTTOM):
        cameras = IotCamera("EZVIZ and\nONVIF cameras")
        v380 = IotCamera("V380 cameras")

    browser >> Edge(label="HTTPS: console, API") >> web
    phone >> Edge(label="HTTPS: API, SSE") >> web
    connector >> Edge(label="HTTPS: heartbeats,\ndiscovery") >> web
    web >> Edge(label="HTTP: /api") >> backend
    analytics >> Edge(label="HTTP: config poll,\nface gallery") >> backend
    analytics >> Edge(label="NATS: events") >> nats
    backend >> Edge(label="NATS: publish,\nconsume") << nats
    backend >> Edge(label="SQL") >> postgres
    backend >> Edge(label="HTTP API:\nstreams") >> go2rtc
    backend >> Edge(label="ONVIF,\nsiren API") >> bridges
    backend >> Edge(label="Telegram\nBot API") >> telegram
    backend >> Edge(label="FCM\nHTTP v1") >> fcm
    backend >> Edge(label="ONVIF: events,\nsettings, PTZ", minlen="2") >> cameras
    bridges >> Edge(label="V380 protocol") >> v380
