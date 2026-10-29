#!/usr/bin/env python3
"""
Subscriber ROS2 - Pengujian QoS Micro-ROS ESP32
================================================
PRINSIP: catat SEMUA data mentah, TIDAK ADA yang dibuang saat pengambilan.
Pembersihan & koreksi dilakukan nanti saat analisis.

Format payload dari ESP32:
  data[0] = seq_num
  data[1] = waktu kirim (detik Unix, NTP)
  data[2] = waktu kirim (milidetik 0-999)
  data[3] = node_id (1-7)

Kolom CSV output:
  node_id, seq_num, t_send_ms, t_receive_ms, latency_ms

Cara pakai:
  python3 subscriber_qos.py <NAMA_SKENARIO> <reliable|best_effort>
  Contoh: python3 subscriber_qos.py R-Q5-P112 reliable

BERHENTI: tekan Ctrl+C (JANGAN Ctrl+backslash)
"""

import csv
import os
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.executors import ExternalShutdownException
from std_msgs.msg import Int32MultiArray

OUTPUT_DIR = '/media/sf_skripsi_van'
TOPIC      = '/esp32_counter'


class QoSSubscriber(Node):
    def __init__(self, scenario, qos_type):
        super().__init__('qos_subscriber')
        self.scenario  = scenario
        self.finished  = False
        self.t_start   = None
        self.total     = 0
        self.per_node  = {}          # node_id -> dict(count, max_seq, lat_sum)
        self.warned_fw = False

        if qos_type.lower() in ('reliable', 'r'):
            reliability, label = ReliabilityPolicy.RELIABLE, 'RELIABLE'
        elif qos_type.lower() in ('best_effort', 'besteffort', 'be'):
            reliability, label = ReliabilityPolicy.BEST_EFFORT, 'BEST_EFFORT'
        else:
            print(f"QoS '{qos_type}' tidak dikenal. Pakai: reliable / best_effort")
            sys.exit(1)

        qos = QoSProfile(reliability=reliability,
                         history=HistoryPolicy.KEEP_LAST,
                         depth=100)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.csv_path = os.path.join(OUTPUT_DIR, f'{scenario}.csv')
        self.f = open(self.csv_path, 'w', newline='')
        self.w = csv.writer(self.f)
        self.w.writerow(['node_id', 'seq_num', 't_send_ms',
                         't_receive_ms', 'latency_ms'])
        self.f.flush()

        self.create_subscription(Int32MultiArray, TOPIC, self.callback, qos)
        self.create_timer(10.0, self.progress)

        print('=' * 60)
        print(f'  Skenario : {scenario}')
        print(f'  QoS      : {label}')
        print(f'  Output   : {self.csv_path}')
        print('  Stop     : Ctrl+C')
        print('=' * 60)
        print('Menunggu data dari ESP32...\n')

    def callback(self, msg):
        t_receive_ms = time.time() * 1000.0     # dicatat paling awal

        d = msg.data
        if len(d) < 4:
            return

        seq_num = int(d[0])
        ts_sec  = int(d[1])
        ts_ms   = int(d[2])
        node_id = int(d[3])

        if ts_sec < 1600000000 and not self.warned_fw:
            print('[PERINGATAN] Timestamp ESP32 bukan waktu NTP. '
                  'Cek firmware sudah versi baru & hotspot ada internet.')
            self.warned_fw = True

        t_send_ms  = ts_sec * 1000.0 + ts_ms
        latency_ms = t_receive_ms - t_send_ms

        # Tulis SEMUA data, tanpa filter
        self.w.writerow([node_id, seq_num, round(t_send_ms, 3),
                         round(t_receive_ms, 3), round(latency_ms, 3)])
        self.f.flush()

        if self.t_start is None:
            self.t_start = time.time()
        self.total += 1
        s = self.per_node.setdefault(node_id,
                                     {'count': 0, 'max_seq': 0, 'lat_sum': 0.0})
        s['count']   += 1
        s['max_seq']  = max(s['max_seq'], seq_num)
        s['lat_sum'] += latency_ms

    def progress(self):
        if self.t_start is None:
            print('  ... belum ada paket masuk')
            return
        el = time.time() - self.t_start
        nodes = ', '.join(f'n{k}:{v["count"]}'
                          for k, v in sorted(self.per_node.items()))
        print(f'[{el:5.0f}s] total {self.total:,} paket | {nodes}')

    def finish(self):
        if self.finished:
            return
        self.finished = True
        try:
            self.f.flush()
            self.f.close()
        except Exception:
            pass

        el = time.time() - self.t_start if self.t_start else 0
        print('\n' + '=' * 60)
        print(f'  SELESAI : {self.scenario}   ({el:.0f} detik)')
        print(f'  File    : {self.csv_path}')
        print('-' * 60)
        print(f'  {"Node":<6}{"Diterima":>10}{"Dikirim":>10}{"Loss":>9}{"Lat.avg":>12}')
        tot_rx = tot_tx = 0
        for nid, s in sorted(self.per_node.items()):
            rx, tx = s['count'], s['max_seq']
            loss = (tx - rx) / tx * 100 if tx else 0
            avg  = s['lat_sum'] / rx if rx else 0
            tot_rx += rx
            tot_tx += tx
            print(f'  {nid:<6}{rx:>10,}{tx:>10,}{loss:>8.2f}%{avg:>10.1f} ms')
        if tot_tx:
            print('-' * 60)
            print(f'  {"TOTAL":<6}{tot_rx:>10,}{tot_tx:>10,}'
                  f'{(tot_tx - tot_rx) / tot_tx * 100:>8.2f}%')
        print('=' * 60)
        print('  Catatan: angka di atas masih mentah (ringkasan cepat).')
        print('  Hasil final dihitung ulang saat analisis data.')


def main():
    if len(sys.argv) < 3:
        print('Cara pakai: python3 subscriber_qos.py <NAMA_SKENARIO> <reliable|best_effort>')
        print('Contoh    : python3 subscriber_qos.py R-Q5-P112 reliable')
        sys.exit(1)

    rclpy.init()
    node = QoSSubscriber(sys.argv[1], sys.argv[2])
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.finish()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
