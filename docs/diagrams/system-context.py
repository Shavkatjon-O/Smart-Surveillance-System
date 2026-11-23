"""System context: the people who use the system and the systems around it."""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotCamera
from diagrams.custom import Custom
from diagrams.firebase.grow import Messaging
from diagrams.generic.os import Android
from diagrams.onprem.client import User, Users
from diagrams.onprem.network import Internet
from diagrams.saas.chat import Telegram

MARK = str(Path(__file__).resolve().parents[2] / "packages" / "brand" / "mark-dark.svg")
BOTTOM = {"labelloc": "b"}

with Diagram(direction="TB", show=False):
    with Cluster("People"):
        owner = Users("Site owner\nor operator")
        admin = User("Platform\nadministrator")
    system = Custom("Smart Surveillance\nSystem", MARK)
    with Cluster("Site LAN", graph_attr=BOTTOM):
        cameras = IotCamera("IP cameras\nONVIF, EZVIZ")
        v380 = IotCamera("V380 cameras")
        tv = Android("Android TV")
    with Cluster("Alert and mail services", graph_attr=BOTTOM):
        telegram = Telegram("Telegram")
        fcm = Messaging("FCM")
        ntfy = Internet("ntfy")
        mail = Internet("Mail server")

    owner >> Edge(label="HTTPS: watches,\ncontrols, investigates") >> system
    admin >> Edge(label="HTTPS:\nadministers") >> system
    system >> Edge(label="RTSP, ONVIF", minlen="2") >> cameras
    system >> Edge(label="V380\nprotocol", minlen="2") >> v380
    cameras >> Edge(label="RTSP") >> tv
    v380 >> Edge(label="V380") >> tv
    system >> Edge(label="Telegram\nBot API", minlen="2") >> telegram
    system >> Edge(label="FCM\nHTTP v1", minlen="2") >> fcm
    system >> Edge(label="HTTP\npush", minlen="2") >> ntfy
    system >> Edge(label="SMTP", minlen="2") >> mail
