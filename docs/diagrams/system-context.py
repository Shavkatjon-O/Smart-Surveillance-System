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

with Diagram(show=False, graph_attr={"column": "215", "row": "110"}):
    with Cluster("Site LAN"):
        v380 = IotCamera("V380 cameras", grid="0,0")
        cameras = IotCamera("Cameras\nONVIF, EZVIZ", grid="0,1")
        tv = Android("TV app\nAndroid TV", grid="0,2.25")
    with Cluster("Remote sites"):
        remote = IotCamera("Cameras\nvia a connector", grid="0,3.6")
    system = Custom("Smart Surveillance\nself-hosted platform", MARK, grid="1,1")
    with Cluster("People"):
        admin = User("Administrator", grid="3,1")
        owner = Users("Site owner\noperator", grid="2,1")
    with Cluster("Notification services"):
        telegram = Telegram("Telegram", grid="2,2.35")
        push = Messaging("Push\nFCM, ntfy", grid="3,2.35")
        mail = Internet("Mail\nSMTP", grid="2,3.35")

    cameras >> Edge(step="Video and events from the cameras", main="true") >> system
    v380 >> Edge(step="V380 cameras through V380 bridges") >> system
    remote >> Edge(step="Remote cameras through a connector") >> system
    system >> Edge(step="Web console and mobile app for site owners and administrators", main="true") >> owner
    system >> Edge(step="Alerts by Telegram and push, account mail by SMTP") >> telegram
    cameras >> Edge(step="TV app plays the cameras directly", style="dashed") >> tv
