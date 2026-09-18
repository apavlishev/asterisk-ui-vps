#!/usr/bin/env python3
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from autodialer_engine import AutoDialerEngine

def main():
    if len(sys.argv) < 6:
        print("Usage: hangup_handler.py <camp_id> <phone> <uniqueid> <cause> <dialstatus> [disposition] [billsec] [duration] [dtmf]")
        sys.exit(0)

    camp_id = sys.argv[1]
    phone = sys.argv[2]
    uniqueid = sys.argv[3]
    cause = sys.argv[4]
    dialstatus = sys.argv[5]
    disposition = sys.argv[6] if len(sys.argv) > 6 else ""
    billsec = sys.argv[7] if len(sys.argv) > 7 else "0"
    duration = sys.argv[8] if len(sys.argv) > 8 else "0"
    dtmf = sys.argv[9] if len(sys.argv) > 9 else ""

    print(f"[autodial_hangup] Camp: {camp_id}, Phone: {phone}, Cause: {cause}, Status: {dialstatus}, Disp: {disposition}, Billsec: {billsec}, DTMF: {dtmf}")

    engine = AutoDialerEngine()
    engine.record_call_result(
        camp_id=camp_id,
        phone=phone,
        uniqueid=uniqueid,
        cause_code=cause,
        dialstatus=dialstatus,
        disposition=disposition,
        billsec=billsec,
        duration=duration,
        dtmf=dtmf
    )

if __name__ == '__main__':
    main()
