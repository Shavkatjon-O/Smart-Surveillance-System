"""Analytics pipeline: the stages of the analytics service from the camera stream to an event and its clip."""

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.generic.storage import Storage
from diagrams.onprem.compute import Server
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi

with Diagram(show=False, graph_attr={"column": "205", "row": "110"}):
    media = Server("Media server\ngo2rtc", grid="0,0.5")
    with Cluster("Analytics container"):
        with Cluster("DeepStream on the GPU"):
            detect = Node("Detect and track\nRF-DETR, NvDCF", grid="1,0.5")
        recorder = Node("Recorder\n2 s segments", grid="1,1.6")
        rules = Node("Rules\nzones, lines, faces", grid="2,0.5")
        clips = Node("Clips\nH.264, NVENC", grid="2,1.6")
    api = Fastapi("API\nconfiguration", grid="3,-0.6")
    bus = Nats("Event bus\nNATS JetStream", grid="3,0.5")
    volume = Storage("Media files\nevents volume", grid="3,1.6")

    media >> Edge(step="Original H.265, decoded once on NVDEC", main="true") >> detect
    detect >> Edge(step="Tracked people, vehicles and animals", main="true") >> rules
    rules >> Edge(step="Event changes and observations", main="true") >> bus
    api >> Edge(step="Configuration poll every 5 s over HTTP", style="dashed") >> rules
    media >> Edge(step="A copy of the stream, not re-encoded") >> recorder
    recorder >> Edge(step="Two-second segments in memory") >> clips
    rules >> Edge(step="Each event asks for its snapshot and clip") >> clips
    clips >> Edge(step="JPEG and H.264 MP4 files") >> volume
