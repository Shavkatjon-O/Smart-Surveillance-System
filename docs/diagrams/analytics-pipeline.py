"""Analytics pipeline: the stages of the analytics service from the camera stream to an event and its clip."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.iot import IotGeneric, IotHardwareBoard
from diagrams.generic.compute import Rack
from diagrams.generic.storage import Storage
from diagrams.onprem.compute import Server
from diagrams.onprem.queue import Nats
from diagrams.programming.framework import Fastapi
from diagrams.programming.language import Python

BOTTOM = {"labelloc": "b"}
INVISIBLE = {"style": "invis"}
ROW = {"constraint": "false"}

with Diagram(direction="TB", show=False):
    go2rtc = Server("go2rtc\nRTSP server")
    with Cluster("analytics container", graph_attr=BOTTOM):
        with Cluster("DeepStream 9.1 child process, NVIDIA GPU"):
            decode = IotGeneric("Decode once\non NVDEC")
            lenses = IotGeneric("One source\nper lens")
            rate = IotGeneric("Analysis rate\nper view")
            detector = IotHardwareBoard("RF-DETR Medium\nNvDCF tracker")
        recorder = Rack("Recorder\nffmpeg, 2 s\nsegments")
        rules = Python("Rules engine\nzones, lines, faces,\nfeatures, tamper")
        clips = IotGeneric("Snapshot and\nH.264 clip\non NVENC")
    events = Storage("events volume")
    nats = Nats("NATS JetStream")
    backend = Fastapi("backend")

    go2rtc >> Edge(label="RTSP: original H.265") >> decode
    go2rtc >> Edge(**INVISIBLE) >> [lenses, rate, detector]
    decode >> Edge(**ROW) >> lenses >> Edge(**ROW) >> rate >> Edge(**ROW) >> detector
    go2rtc >> Edge(label="RTSP: copy") >> recorder
    detector >> Edge(label="tracked\nobjects") >> rules
    recorder >> Edge(label="segments") >> clips
    rules >> Edge(label="event", **ROW) >> clips
    clips >> Edge(label="JPEG, MP4") >> events
    rules >> Edge(label="NATS: events") >> nats
    rules >> Edge(label="HTTP: config\nevery 5 s") >> backend
