#!/usr/bin/env python3
"""Checks the Linphone side of the PJSIP config without a real phone:
registers as the phone endpoint with digest auth, confirms Asterisk sees it online,
then has Asterisk ring it and confirms the INVITE arrives (we answer 486 Busy).
Also checks a wrong password is refused."""
import hashlib
import random
import re
import socket
import subprocess
import sys
import time

AST = ("127.0.0.1", 5060)
USER, PASSWORD = "ly", sys.argv[1] if len(sys.argv) > 1 else "testpass"


def ast(cmd):
    return subprocess.run(["asterisk", "-rx", cmd], capture_output=True, text=True).stdout


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def register(sock, password, port):
    call_id = f"{random.getrandbits(64):x}@127.0.0.1"
    tag = f"{random.getrandbits(32):x}"

    def req(cseq, auth=""):
        return (f"REGISTER sip:127.0.0.1 SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP 127.0.0.1:{port};branch=z9hG4bK{random.getrandbits(40):x};rport\r\n"
                f"Max-Forwards: 70\r\nFrom: <sip:{USER}@127.0.0.1>;tag={tag}\r\nTo: <sip:{USER}@127.0.0.1>\r\n"
                f"Call-ID: {call_id}\r\nCSeq: {cseq} REGISTER\r\nContact: <sip:{USER}@127.0.0.1:{port}>\r\n"
                f"Expires: 60\r\n{auth}Content-Length: 0\r\n\r\n").encode()

    sock.sendto(req(1), AST)
    resp = sock.recv(4096).decode()
    m = re.search(r'WWW-Authenticate: Digest (.*)', resp)
    assert m, resp.splitlines()[0]
    params = dict(re.findall(r'(\w+)="?([^",]+)"?', m.group(1)))
    realm, nonce = params["realm"], params["nonce"]
    ha1 = md5(f"{USER}:{realm}:{password}")
    ha2 = md5("REGISTER:sip:127.0.0.1")
    nc, cnonce = "00000001", f"{random.getrandbits(32):x}"
    qop = params.get("qop")
    if qop:
        response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        auth = (f'Authorization: Digest username="{USER}", realm="{realm}", nonce="{nonce}", '
                f'uri="sip:127.0.0.1", response="{response}", algorithm=MD5, qop=auth, nc={nc}, cnonce="{cnonce}"'
                + (f', opaque="{params["opaque"]}"' if "opaque" in params else "") + "\r\n")
    else:
        response = md5(f"{ha1}:{nonce}:{ha2}")
        auth = (f'Authorization: Digest username="{USER}", realm="{realm}", nonce="{nonce}", '
                f'uri="sip:127.0.0.1", response="{response}", algorithm=MD5\r\n')
    sock.sendto(req(2, auth), AST)
    return sock.recv(4096).decode().splitlines()[0]


def main():
    bad = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bad.bind(("127.0.0.1", 5071))
    bad.settimeout(5)
    status = register(bad, "wrong-password", 5071)
    assert "401" in status or "403" in status, f"wrong password accepted: {status}"
    print("wrong password refused:", status)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 5072))
    s.settimeout(5)
    status = register(s, PASSWORD, 5072)
    assert "200" in status, status
    print("registered:", status)
    # A real phone answers Asterisk's keep-alive OPTIONS pings; do the same for a few seconds.
    end = time.time() + 3
    s.settimeout(0.5)
    while time.time() < end:
        try:
            msg = s.recv(8192).decode()
        except socket.timeout:
            continue
        if msg.startswith("OPTIONS"):
            hdrs = {k.lower(): v for k, v in re.findall(r"^([\w-]+): (.*?)\r?$", msg, re.M)}
            s.sendto((f"SIP/2.0 200 OK\r\nVia: {hdrs['via']}\r\nFrom: {hdrs['from']}\r\nTo: {hdrs['to']};tag=o1\r\n"
                      f"Call-ID: {hdrs['call-id']}\r\nCSeq: {hdrs['cseq']}\r\nContent-Length: 0\r\n\r\n").encode(), AST)
    s.settimeout(5)
    contacts = ast("pjsip show contacts")
    assert "ly/sip:ly@127.0.0.1:5072" in contacts, contacts
    ep = [l for l in ast("pjsip show endpoint ly").splitlines() if l.strip().startswith("Endpoint:  ly")][0]
    assert "Not in use" in ep, ep
    print("endpoint state:", " ".join(ep.split()[2:4]))

    # Ring the phone the same way Connect does.
    ast("channel originate PJSIP/ly application Wait 5")
    deadline = time.time() + 6
    got_invite = False
    while time.time() < deadline:
        try:
            msg = s.recv(8192).decode()
        except socket.timeout:
            break
        first = msg.splitlines()[0]
        hdrs = {k.lower(): v for k, v in re.findall(r"^([\w-]+): (.*?)\r?$", msg, re.M)}
        if first.startswith("OPTIONS") or first.startswith("INVITE"):
            code = "200 OK" if first.startswith("OPTIONS") else "486 Busy Here"
            if first.startswith("INVITE"):
                got_invite = True
                assert "m=audio" in msg, "INVITE has no audio offer"
            reply = (f"SIP/2.0 {code}\r\nVia: {hdrs['via']}\r\nFrom: {hdrs['from']}\r\nTo: {hdrs['to']};tag=x1\r\n"
                     f"Call-ID: {hdrs['call-id']}\r\nCSeq: {hdrs['cseq']}\r\nContent-Length: 0\r\n\r\n")
            s.sendto(reply.encode(), AST)
            if got_invite:
                codecs = re.findall(r"a=rtpmap:\d+ ([\w]+)/", msg)
                print("phone was rung; codecs offered:", ",".join(codecs))
                break
    assert got_invite, "Asterisk never rang the phone"
    print("SIP check OK")


if __name__ == "__main__":
    main()
