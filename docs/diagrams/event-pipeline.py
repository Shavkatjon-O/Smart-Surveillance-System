"""Event pipeline: producers, subjects, the stream, the workers that consume it and where events end up."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.iot import IotCamera, IotGeneric
from diagrams.firebase.grow import Messaging
from diagrams.onprem.client import Client
from diagrams.onprem.database import Postgresql
from diagrams.onprem.network import Internet
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi, Flutter
from diagrams.programming.language import Python
from diagrams.saas.chat import Telegram

BOTTOM = {"labelloc": "b"}

with Diagram(direction="TB", show=False):
    edge = IotGeneric("Edge engines")
    cameras = IotCamera("Cameras")
    analytics = IotGeneric("analytics\nservice")
    intake = Fastapi("API: intake route\n/internal/analytics")
    subscriber = Fastapi("API: camera\nevent subscriber")
    with Cluster("NATS JetStream", graph_attr={"labelloc": "b", "labeljust": "r"}):
        analytics_subject = Node("events.<site>.analytics")
        camera_subject = Node("events.<site>.camera")
        stream = Nats("stream EVENTS\nsubjects events.>")
    with Cluster("Workers", graph_attr=BOTTOM):
        store = Python("event-store")
        notifier = Python("notifier")
        metering = Python("metering")
    with Cluster("API replicas"):
        live = Fastapi("live fan-out")
    postgres = Postgresql("postgres\nevents, usage")
    with Cluster("Alert services", graph_attr=BOTTOM):
        fcm = Messaging("FCM")
        ntfy = Internet("ntfy")
        telegram = Telegram("Telegram")
    with Cluster("Phones and browsers", graph_attr=BOTTOM):
        browser = Client("Browser")
        phone = Flutter("Mobile app")

    cameras >> Edge(label="ONVIF\nPullPoint") >> subscriber
    edge >> Edge(label="HTTPS POST") >> intake
    analytics >> Edge(label="HTTP: config\npoll", style="dashed", constraint="false") >> intake
    analytics >> Edge(label="publish") >> analytics_subject
    intake >> Edge(label="republish") >> analytics_subject
    subscriber >> Edge(label="publish") >> camera_subject
    analytics_subject >> Edge(label="stored") >> stream
    camera_subject >> Edge(label="stored") >> stream
    stream >> Edge(label="durable\npull") >> store
    stream >> Edge(label="durable\npull") >> notifier
    stream >> Edge(label="durable\npull") >> metering
    stream >> Edge(label="ordered, one\nper replica") >> live
    store >> Edge(label="SQL: events") >> postgres
    metering >> Edge(label="SQL: usage") >> postgres
    notifier >> Edge(label="FCM HTTP v1") >> fcm
    notifier >> Edge(label="HTTP") >> ntfy
    notifier >> Edge(label="Bot API") >> telegram
    live >> Edge(label="SSE") >> browser
    live >> Edge(label="SSE") >> phone
