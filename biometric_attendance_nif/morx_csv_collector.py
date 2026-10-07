#!/usr/bin/env python3

from zk import ZK
import csv
import os
import time
import signal
import logging
from datetime import datetime


DEVICE_IP = "192.168.77.201"
DEVICE_PORT = 4370


EMPLOYEE_FILE = "data/employees.csv"
ATTENDANCE_FILE = "data/attendance.csv"


running = True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("MR110")



def stop(sig, frame):
    global running
    logger.info("Stopping collector...")
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)



def connect_device():

    while running:

        try:

            logger.info(
                "Connecting to MR110..."
            )

            zk = ZK(
                DEVICE_IP,
                port=DEVICE_PORT,
                timeout=10
            )

            conn = zk.connect()

            logger.info(
                "Connected"
            )

            return conn


        except Exception as e:

            logger.error(e)

            time.sleep(5)



def save_employee(user):

    file_exists = os.path.exists(
        EMPLOYEE_FILE
    )


    with open(
        EMPLOYEE_FILE,
        "a",
        newline=""
    ) as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow(
                [
                    "id",
                    "name"
                ]
            )


        writer.writerow(
            [
                user.user_id,
                user.name
            ]
        )



def load_users(conn):

    users = {}


    logger.info(
        "Loading employees..."
    )


    for user in conn.get_users():

        users[user.user_id] = user.name

        save_employee(user)


    logger.info(
        "Employees: %s",
        len(users)
    )


    return users




def save_attendance(data):

    file_exists = os.path.exists(
        ATTENDANCE_FILE
    )


    with open(
        ATTENDANCE_FILE,
        "a",
        newline=""
    ) as f:

        writer = csv.writer(f)


        if not file_exists:

            writer.writerow(
                [
                    "employee_id",
                    "name",
                    "timestamp",
                    "status",
                    "punch",
                    "source"
                ]
            )


        writer.writerow(
            [
                data["employee_id"],
                data["name"],
                data["timestamp"],
                data["status"],
                data["punch"],
                data["source"]
            ]
        )



def download_history(conn, users):

    logger.info(
        "Downloading old attendance..."
    )


    logs = conn.get_attendance()


    logger.info(
        "Records found: %s",
        len(logs)
    )


    for log in logs:


        data = {

            "employee_id":
                log.user_id,

            "name":
                users.get(
                    log.user_id,
                    "Unknown"
                ),

            "timestamp":
                log.timestamp,

            "status":
                log.status,

            "punch":
                log.punch,

            "source":
                "HISTORY"
        }


        save_attendance(data)



def live_listener(conn, users):

    logger.info(
        "Waiting for live scans..."
    )


    seen=set()


    for event in conn.live_capture():


        if not running:
            break


        if event is None:
            continue



        key = (
            event.user_id,
            event.timestamp
        )


        if key in seen:
            continue


        seen.add(key)



        data = {

            "employee_id":
                event.user_id,

            "name":
                users.get(
                    event.user_id,
                    "Unknown"
                ),

            "timestamp":
                event.timestamp,

            "status":
                event.status,

            "punch":
                event.punch,

            "source":
                "LIVE"
        }



        save_attendance(data)


        logger.info(
            "LIVE: %s",
            data
        )



def main():

    conn = None


    try:

        conn = connect_device()

        users = load_users(
            conn
        )


        download_history(
            conn,
            users
        )


        live_listener(
            conn,
            users
        )


    except Exception as e:

        logger.error(
            e
        )


    finally:

        if conn:

            conn.disconnect()



if __name__ == "__main__":

    main()