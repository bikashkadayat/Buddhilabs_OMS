#!/usr/bin/env python3

"""
Morx BioTime MR110 Live Attendance Collector

Device:
    IP: 192.168.77.201
    Port: 4370

Protocol:
    ZK compatible

Purpose:
    Collect live attendance events
"""

import time
import signal
import logging
from datetime import datetime

from zk import ZK


# =========================
# CONFIGURATION
# =========================

DEVICE_IP = "192.168.77.201"
DEVICE_PORT = 4370

RECONNECT_DELAY = 5


# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("MR110")


# =========================
# GLOBAL STATE
# =========================

running = True


def shutdown_handler(sig, frame):
    global running

    logger.info(
        "Shutdown signal received"
    )

    running = False



signal.signal(
    signal.SIGINT,
    shutdown_handler
)

signal.signal(
    signal.SIGTERM,
    shutdown_handler
)



# =========================
# DEVICE CONNECTION
# =========================

def connect_device():

    while running:

        try:

            logger.info(
                "Connecting to MR110 %s:%s",
                DEVICE_IP,
                DEVICE_PORT
            )


            zk = ZK(
                DEVICE_IP,
                port=DEVICE_PORT,
                timeout=10
            )


            conn = zk.connect()


            logger.info(
                "Connected successfully"
            )


            return conn


        except Exception as e:

            logger.error(
                "Connection failed: %s",
                e
            )


            time.sleep(
                RECONNECT_DELAY
            )


    return None



# =========================
# USERS
# =========================

def load_users(conn):

    users = {}


    try:

        logger.info(
            "Loading employees..."
        )


        for user in conn.get_users():

            users[user.user_id] = {

                "id": user.user_id,

                "name": user.name

            }


        logger.info(
            "Loaded %s employees",
            len(users)
        )


    except Exception as e:

        logger.error(
            "User loading error: %s",
            e
        )


    return users




# =========================
# HANDLE ATTENDANCE
# =========================

def process_attendance(
        attendance,
        users
):


    if attendance is None:

        return



    employee_id = (
        attendance.user_id
    )


    employee = users.get(
        employee_id,
        {
            "name": "Unknown"
        }
    )


    data = {

        "employee_id":
            employee_id,


        "employee_name":
            employee["name"],


        "timestamp":
            attendance.timestamp,


        "status":
            attendance.status,


        "punch":
            attendance.punch

    }


    #
    # DATABASE INSERT GOES HERE
    #
    # save_attendance(data)
    #


    logger.info(
        "ATTENDANCE %s",
        data
    )




# =========================
# LIVE LISTENER
# =========================

def start_listener(conn, users):


    logger.info(
        "Live listener started"
    )


    processed = set()



    try:

        for attendance in conn.live_capture():

            if not running:

                break



            if attendance is None:

                continue



            event_key = (

                attendance.user_id,

                attendance.timestamp

            )



            if event_key in processed:

                continue



            processed.add(
                event_key
            )



            process_attendance(
                attendance,
                users
            )



    except Exception as e:


        logger.error(
            "Live capture error: %s",
            e
        )



# =========================
# MAIN SERVICE
# =========================

def main():


    logger.info(
        "MR110 Collector Starting"
    )


    while running:


        conn = None


        try:


            conn = connect_device()


            if conn is None:

                break



            users = load_users(
                conn
            )


            start_listener(
                conn,
                users
            )



        except Exception as e:


            logger.error(
                "Service error: %s",
                e
            )



        finally:


            if conn:


                try:

                    conn.disconnect()

                    logger.info(
                        "Device disconnected"
                    )


                except:

                    pass



        if running:


            logger.info(
                "Reconnecting..."
            )


            time.sleep(
                RECONNECT_DELAY
            )



    logger.info(
        "Collector stopped"
    )



if __name__ == "__main__":

    main()
