"""Analytics pipeline: the stages of the analytics service from the camera stream to an event and its clip."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.generic.storage import Storage
from diagrams.onprem.compute import Server
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi

MAIN = {"group": "main"}

with Diagram(direction="LR", show=False):
    media = Server("Media server\ngo2rtc", **MAIN)
    with Cluster("Analytics container"):
        with Cluster("DeepStream on the GPU"):
            detect = Node("Detect and track\nRF-DETR, NvDCF", **MAIN)
        recorder = Node("Recorder\n2 s segments")
        rules = Node("Rules\nzones, lines, faces", **MAIN)
        clips = Node("Clips\nH.264, NVENC")
    api = Fastapi("API\nconfiguration")
    bus = Nats("Event bus\nNATS JetStream", **MAIN)
    volume = Storage("Media files\nevents volume")

    media >> Edge(label="H.265", main="true") >> detect
    media >> Edge(label="Copy") >> recorder
    detect >> Edge(label="Tracks", main="true") >> rules
    recorder >> Edge(label="Segments") >> clips
    rules >> Edge(constraint="false") >> clips
    rules << Edge(label="Config poll", style="dashed") << api
    rules >> Edge(label="Events", main="true") >> bus
    clips >> Edge(label="JPEG, MP4") >> volume
