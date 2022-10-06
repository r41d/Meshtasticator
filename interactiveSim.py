""" Simulator for letting multiple instances of native programs 
    communicate via TCP as if they did via their LoRa chip. 
    Usage: python3 interactiveSim.py [nrNodes] [--p <full-path-to-program>]
"""

import sys
import os
import time
from lib.common import *
from lib.phy import estimatePathLoss
import select
from pubsub import pub
import meshtastic.tcp_interface
from meshtastic import mesh_pb2
HW_ID_OFFSET = 16
TCP_PORT_OFFSET = 4403


class interactiveNode(): 
    def __init__(self, nodes, nodeId, hwId, TCPPort, x=-1, y=-1):
        self.nodeid = nodeId
        self.x = x
        self.y = y
        self.hwId = hwId
        self.TCPPort = TCPPort 
        if self.x == -1 and self.y == -1:
            self.x, self.y = findRandomPosition(nodes)

    def addInterface(self, iface):
        self.iface = iface


class realPacket():
    def __init__(self, packet, id):
        self.packet = packet
        self.localId = id
        
    def setTxRxs(self, transmitter, receivers):
        self.transmitter = transmitter
        self.receivers = receivers


def forwardPacket(iface, packet, rssi, snr): 
    data = packet["decoded"]["payload"]
    if getattr(data, "SerializeToString", None):
        data = data.SerializeToString()

    if len(data) > mesh_pb2.Constants.DATA_PAYLOAD_LEN:
        raise Exception("Data payload too big")

    meshPacket = mesh_pb2.MeshPacket()
    meshPacket.decoded.payload = data
    meshPacket.to = packet["to"]
    setattr(meshPacket, "from", packet["from"])
    meshPacket.id = packet["id"]
    if "want_ack" in packet:
        meshPacket.want_ack = packet["want_ack"]
    else:
        meshPacket.want_ack = False
    meshPacket.decoded.portnum = 69
    if "hopLimit" in packet:
        meshPacket.hop_limit = packet["hopLimit"]
    if "want_response" in packet:
        meshPacket.decoded.want_response = packet["want_response"]
    else:
        meshPacket.decoded.want_response = False
    meshPacket.rx_rssi = int(rssi) 
    meshPacket.rx_snr = int(snr)  
    toRadio = mesh_pb2.ToRadio()
    toRadio.packet.CopyFrom(meshPacket)
    iface._sendToRadio(toRadio)


def onReceive(interface, packet): 
    global messageId
    existingMsgId = next((m.localId for m in messages if m.packet["id"] == packet["id"]), None)
    if existingMsgId != None:
        mId = existingMsgId
    else: 
        messageId += 1
        mId = messageId
    rP = realPacket(packet, mId)
    messages.append(rP)
    print("Node", interface.myInfo.my_node_num-HW_ID_OFFSET, "sent", packet["decoded"]["simulator"]["portnum"], "with id", mId, "over the air!")
    # TODO forward only to those nodes that are in range 
    transmitter = next((n for n in nodes if n.TCPPort == interface.portNumber), None)
    receivers = [n for n in nodes if n.nodeid != transmitter.nodeid]
    rxs, rssis, snrs = calcReceivers(transmitter, receivers)
    rP.setTxRxs(transmitter, rxs)
    for i,r in enumerate(rxs):
        forwardPacket(r.iface, packet, rssis[i], snrs[i])
    graph.packets.append(rP)


def calcReceivers(tx, receivers): 
    rxs = []
    rssis = []
    snrs = []
    for rx in receivers:
        dist_2d = calcDist(tx.x, tx.y, rx.x, rx.y) 
        pathLoss = estimatePathLoss(dist_2d, conf.FREQ)
        RSSI = conf.PTX + conf.GL - pathLoss
        SNR = RSSI-conf.NOISE_LEVEL
        if RSSI >= conf.SENSMODEM[conf.MODEM]:
            rxs.append(rx)
            rssis.append(RSSI)
            snrs.append(SNR)
    return rxs, rssis, snrs


def closeNodes():
    print("\nClosing all nodes...")
    pub.unsubAll()
    for n in nodes:
        n.iface.close()
    os.system("killall "+pathToProgram+"program")


if len(sys.argv) < 2:
    [xs, ys] = genScenario()
    conf.NR_NODES = len(xs)
    pathToProgram = os.getcwd()+"/"
else:
    if int(sys.argv[1]) > 10:
        print("Not sure if you want to start more than 10 terminals. Exiting.")
        exit(1)
    conf.NR_NODES = int(sys.argv[1])
    xs = []
    ys = []
    if len(sys.argv) > 2 and type(sys.argv[2]) == str and ("--p" in sys.argv[2]):
        string = sys.argv[3]
        pathToProgram = string
    else:
        pathToProgram = os.getcwd()+"/"

nodes = []
graph = Graph()
for n in range(conf.NR_NODES):
    if len(xs) == 0: 
        node = interactiveNode(nodes, n, n+HW_ID_OFFSET, n+TCP_PORT_OFFSET)
    else:
        node = interactiveNode(nodes, n, n+HW_ID_OFFSET, n+TCP_PORT_OFFSET, xs[n], ys[n])
    nodes.append(node)
    graph.addNode(node)

for n in nodes:
    cmdString = "gnome-terminal --title='Node "+str(n.nodeid)+"' -- "+pathToProgram+"program -e -d "+os.path.expanduser('~')+"/.portduino/node"+str(n.nodeid)+" -h "+str(n.hwId)+" -p "+str(n.TCPPort)
    os.system(cmdString) 

messages = []
global messageId
messageId = -1
time.sleep(4)  # Allow instances to start up their TCP service 
try:
    for n in nodes:
        iface = meshtastic.tcp_interface.TCPInterface(hostname="localhost", portNumber=n.TCPPort)
        n.addInterface(iface)
    pub.subscribe(onReceive, "meshtastic.receive.simulator")
except(Exception) as ex:
    print(f"Error: Could not connect to native program: {ex}")
    for n in nodes:
        n.iface.close()
    os.system("killall "+pathToProgram+"program")
    sys.exit(1)


try:
    time.sleep(15)  # Wait until nodeInfo messages are sent
    #text = "Hi there, how are you doing?"
    #nodes[0].iface.sendText(text)
    #time.sleep(20)
    # Add any additional messaging here
    closeNodes()
except KeyboardInterrupt:
    closeNodes()

graph.initRoutes()
