"""System context: the systems around Smart Surveillance System and the people who use it."""

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
MAIN = {"group": "main"}

with Diagram(direction="LR", show=False, graph_attr={"ranksep": "1"}):
    with Cluster("Site LAN"):
        v380 = IotCamera("V380 cameras")
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", **MAIN)
        tv = Android("TV app\nAndroid TV")
    with Cluster("Remote sites"):
        remote = IotCamera("Cameras\nvia a connector")
    system = Custom("Smart Surveillance\nself-hosted platform", MARK, **MAIN)
    with Cluster("People") as people:
        admin = User("Administrator")
        owner = Users("Site owner\noperator", **MAIN)
    with Cluster("Notification services") as services:
        telegram = Telegram("Telegram")
        push = Messaging("Push\nFCM, ntfy")
        mail = Internet("Mail\nSMTP")

    cameras >> Edge(label="Video, events", main="true") >> system
    v380 >> Edge(label="V380") >> system
    cameras >> Edge(style="dashed", constraint="false") >> tv
    remote >> Edge(label="Connector") >> system
    system >> Edge(label="Console, app", main="true", lhead=people.name) >> owner
    system >> Edge(label="Alerts, mail", lhead=services.name) >> push
    system >> Edge(style="invis") >> [admin, telegram, mail]
