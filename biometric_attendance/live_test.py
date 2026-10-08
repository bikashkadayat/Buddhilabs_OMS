from zk import ZK

zk = ZK(
    "192.168.77.201",
    port=4370,
    timeout=5
)

conn = zk.connect()

print("Waiting for attendance...")

for attendance in conn.live_capture():
    print(attendance)
