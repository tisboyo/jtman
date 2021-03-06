import pywsjtx.extra.simple_server
import threading
from pyhamtools.utils import freq_to_band
import requests
import re
import random
from datetime import datetime,timedelta
import pandas
from termcolor import colored
from logger import LOGGER as log


class Listener:
    def __init__(self,q,config,ip_address,port,timeout=2.0):
        log.debug('new listener: '+str(q))
        self.config = config
        self.band = None
        self.lastReport = datetime.now()
        self.lastScan = None
        self.q = q
        self.unseen = []
        self.unlogged = []
        self.stopped = False
        self.ifttt_key = self.config.get('OPTS','ifttt_key')
        self.ip_address = ip_address
        self.port = port

        self.initAdif()
        self.s = pywsjtx.extra.simple_server.SimpleServer(ip_address, port)

    def initAdif(self):
        filePaths = self.config.get('ADIF_FILES','paths').splitlines()
        if self.config.get('OPTS','load_adif_files_on_start'):
            for filepath in filePaths:
                self.q.addAdifFile(filepath,True)
            if self.config.get('LOTW','enable'):
                username = self.config.get('LOTW','username')
                password = self.config.get('LOTW','password')
                if username and password:
                    self.q.loadLotw(username,password)


    def ifttt_event(self,event):
        if self.ifttt_key:
            requests.post('https://maker.ifttt.com/trigger/'+event+'/with/key/'+self.ifttt_key)

    def print_line(self):
        now = datetime.now()
        newLastReport = datetime(now.year, now.month, now.day, now.hour, now.minute, 15*(now.second // 15),0)
        if (newLastReport-self.lastReport).total_seconds() >= 15:
            log.info("------- "+str(newLastReport)+" -------")
        self.lastReport = newLastReport

    def send_reply(self,data):
        packet = pywsjtx.ReplyPacket.Builder(data['packet'])
        self.s.send_packet(data['addr_port'], packet)

    def parse_packet(self):
        #print('decode packet ',self.the_packet)

        m = re.match(r"^CQ\s(\w{2}\b)?\s?([A-Z0-9/]+)\s([A-Z0-9/]+)?\s?([A-Z]{2}[0-9]{2})", self.the_packet.message)
        if m:
            #print("Callsign {}".format(m.group(1)))
            callsign = m.group(2)
            grid = m.group(4)
            #print("CALL ",callsign,' on ',self.band)

            self.print_line()

            msg = callsign
            needData = self.q.needDataByBandAndCall(self.band,callsign)
            needData['call'] = callsign
            needData['grid'] = grid
            needData['cq'] = True
            needData['packet'] = self.the_packet
            needData['addr_port'] = self.addr_port
            self.unseen.append(needData)

            if needData['newState'] == True:
                log.info(colored("NEW STATE {} {}".format(callsign,needData['state']), 'magenta', 'on_white'))
                bg=pywsjtx.QCOLOR.RGBA(255,255,0,0)
                fg=pywsjtx.QCOLOR.Black()
                self.ifttt_event('qso_was')
            elif needData['newDx'] == True:
                log.info(colored("NEW DX {} {} {}".format(callsign,needData['dx'],needData['country']), 'red', 'on_white'))
                bg=pywsjtx.QCOLOR.Red()
                fg=pywsjtx.QCOLOR.White()
                self.ifttt_event('qso_dxcc')
            elif needData['newCall'] == True:
                log.info(colored("NEW CALL {} {} {}".format(callsign,needData['state'],needData['country']), 'white', 'on_blue'))
                bg=pywsjtx.QCOLOR.RGBA(255,0,0,255)
                fg=pywsjtx.QCOLOR.White()
                msg = msg + ' NEW CALL'
            else:
                bg=pywsjtx.QCOLOR.Uncolor()
                fg=pywsjtx.QCOLOR.Uncolor()
                msg = msg + '_'

            color_pkt = pywsjtx.HighlightCallsignPacket.Builder(self.the_packet.wsjtx_id, callsign, bg, fg, True)
            self.s.send_packet(self.addr_port, color_pkt)
        else:
            m = re.match(r"([A-Z0-9/]+) ([A-Z0-9/]+)", self.the_packet.message)
            if m:
                call1 = m.group(1)
                call2 = m.group(2)
                needData = self.q.needDataByBandAndCall(self.band,call1)
                needData['call'] = call1
                needData['cq'] = False
                self.unseen.append(needData)
                needData = self.q.needDataByBandAndCall(self.band,call2)
                needData['call'] = call2
                needData['cq'] = False
                self.unseen.append(needData)

        pass

    def stop(self):
        log.debug("stopping wsjtx listener")
        self.stopped = True
        #self.t.join()


    def doListen(self):
        while True:
            if self.stopped:
                break
            (self.pkt, self.addr_port) = self.s.rx_packet()
            if (self.pkt != None):
                self.the_packet = pywsjtx.WSJTXPacketClassFactory.from_udp_packet(self.addr_port, self.pkt)
                self.handle_packet()
            self.pkt = None
            self.the_packet = None
            self.addr_port = None

    def listen(self):
        self.t = threading.Thread(target=self.doListen)
        log.info("Listener started "+self.ip_address+":"+str(self.port))
        self.t.start()



    def heartbeat(self):
        max_schema = max(self.the_packet.max_schema, 3)
        reply_beat_packet = pywsjtx.HeartBeatPacket.Builder(self.the_packet.wsjtx_id,max_schema)
        self.s.send_packet(self.addr_port, reply_beat_packet)

    def update_status(self):
        #print('status ',self.the_packet)
        try:
            bandinfo = freq_to_band(self.the_packet.dial_frequency/1000)
            self.band = str(bandinfo['band'])+'M'
        except Exception as e:
            pass

    def update_log(self):
        log.info("update log".format(self.the_packet))
        self.unlogged.append(self.the_packet.call)

    def handle_packet(self):
        if type(self.the_packet) == pywsjtx.HeartBeatPacket:
            self.heartbeat()
        elif type(self.the_packet) == pywsjtx.StatusPacket:
            self.update_status()
        elif type(self.the_packet) == pywsjtx.QSOLoggedPacket:
            self.update_log()
        elif self.band != None:
            if type(self.the_packet) == pywsjtx.DecodePacket:
                self.parse_packet()
        else:
            log.debug('unknown packet type {}; {}'.format(type(self.the_packet),self.the_packet))

